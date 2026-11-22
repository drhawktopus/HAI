"""a1_order end to end with a SCRIPTED model on the real harness, the real grader and real Polyglot exercises: the
positive arm writes the reference solution through `write_file` and finishes (must PASS); the negative arm finishes
without writing (must fail); the control, results.json, transcripts and progress lines are checked."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

A1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOUNDRY_MIRROR = r"E:\SSDShared\mothership-build"
sys.path[:0] = [A1, os.path.join(os.path.dirname(A1), "inspect_lab"), FOUNDRY_MIRROR]   # lab_provider ships beside a1_order
os.environ.setdefault("FOUNDRY_ROOT", tempfile.mkdtemp())
import a1_order as O  # noqa: E402

EX = "two-fer" if os.path.isdir(os.path.join(O.EXERCISES, "two-fer")) else sorted(os.listdir(O.EXERCISES))[0]


def scripted(write_reference):
    ex_dir = os.path.join(O.EXERCISES, EX)
    example = open(os.path.join(ex_dir, ".meta", "example.py"), encoding="utf-8").read()
    calls = []

    def fake_chat(host, model, messages, options, think=False, tools=None, **kw):
        calls.append(len(messages))
        if write_reference and len(calls) == 1:
            tc = {"function": {"name": "write_file", "arguments": {"path": EX.replace("-", "_") + ".py",
                                                                   "content": example}}}
        else:
            tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
        return {"message": {"role": "assistant", "content": "", "thinking": "x" * 7, "tool_calls": [tc]},
                "eval_count": 20, "eval_duration": 1e9, "done_reason": "stop",
                "load_duration": 2.5e9 if len(calls) == 1 else 0, "prompt_eval_duration": 4e8}
    return fake_chat


class FakeMeter:
    """infer_ollama.PowerMeter's contract: `source`, `samples`, `joules(t0, t1)`, `stop()`. Live sources price a row
    at once; "telemetry-csv" (the AMD box) only after stop()."""
    source = "nvidia-smi@4Hz"

    def __init__(self):
        self.samples, self.stopped = [(0, 100.0)], False

    def joules(self, t0, t1):
        if self.source == "telemetry-csv" and not self.stopped:
            return None
        return 123.4

    def stop(self):
        self.stopped = True


class A1OrderEndToEnd(unittest.TestCase):

    def run_order(self, write_reference, meter=FakeMeter):
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
        buf = io.StringIO()
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", scripted(write_reference)), \
                mock.patch.object(O.IO, "PowerMeter", meter), mock.patch.object(O.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", buf):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        return rc, res, buf.getvalue(), out

    def test_a_model_that_writes_the_reference_passes(self):
        rc, res, log, out = self.run_order(True)
        self.assertEqual(rc, 0)
        self.assertTrue(res["control"]["passed"], res["control"])
        self.assertEqual((res["solved"], res["answered"], res["total"]), (1, 1, 1))
        row = res["rows"][0]
        self.assertTrue(row["passed"])
        self.assertEqual(row["eval_tokens"], 40)
        self.assertIn("[a1] 1/1 %s PASS" % EX, log, "the foundry's progress protocol line")
        line = next(l for l in log.splitlines() if l.startswith("[a1] 1/1"))
        import p20_foundry as PF                         # the factory's REAL parser reads the running score
        sm = PF.COMPUTE_SOLVED_RE.search(line)
        self.assertEqual((sm.group(1), sm.group(2)), ("1", "1"), line)
        t = json.load(open(os.path.join(out, "transcripts", EX + ".json"), encoding="utf-8"))
        self.assertEqual(t["turns"][0]["tool_calls"][0]["name"], "write_file")
        self.assertEqual(t["turns"][0]["thinking_chars"], 7)
        self.assertEqual((t["turns"][0]["load_s"], t["turns"][0]["prompt_eval_s"]), (2.5, 0.4),
                         "a reload and a re-read are recorded per turn, never hidden in the exercise")
        self.assertIsNone(t["turns"][1]["load_s"], "no load on a warm turn")
        self.assertFalse(os.path.exists(os.path.join(out, "work")), "temp workspaces are a byproduct: removed")

    def test_the_tile_rate_is_the_engines_decode_rate_not_tokens_over_wall(self):
        """the tile read 21.8 tok/s for an A1 run whose engine decoded at 134-142 - the line printed tokens
        / turn wall (conversation re-reads included). Every tile's tok/s is the decode rate (one derivation)."""
        rc, res, log, out = self.run_order(True)
        row = res["rows"][0]
        t = json.load(open(os.path.join(out, "transcripts", EX + ".json"), encoding="utf-8"))["turns"]
        dec = [x for x in t if x.get("eval_count") and x.get("tok_s")]
        self.assertTrue(dec, "the scripted turns carry server decode stats")
        want = sum(x["eval_count"] for x in dec) / sum(x["eval_count"] / x["tok_s"] for x in dec)
        self.assertAlmostEqual(row["decode_tok_s"], want, places=1)
        line = next(l for l in log.splitlines() if l.startswith("[a1] 1/1"))
        import p20_foundry as PF
        self.assertAlmostEqual(float(PF.COMPUTE_TOKS_RE.search(line).group(1)), row["decode_tok_s"], places=1)
        self.assertIn("tok_s", row, "the effective tokens-over-wall figure stays in the row under its old name")

    def test_each_row_carries_energy_and_stamps_for_phase7_and_35(self):
        rc, res, log, out = self.run_order(True)
        row = res["rows"][0]
        self.assertEqual(row["gpu_j"], 123.4, "a live meter prices the row as soon as it ends")
        self.assertTrue(row["at"] and row["t1"] >= row["t0"])
        self.assertEqual(res["task_set"], O.TASK_SET)
        self.assertEqual(len(res["harness_sha256"]), 16)
        self.assertEqual(res["energy"]["source"], "nvidia-smi@4Hz")

    def test_the_amd_meter_prices_rows_at_stop(self):
        class AmdMeter(FakeMeter):
            source = "telemetry-csv"
        rc, res, log, out = self.run_order(True, meter=AmdMeter)
        self.assertEqual(res["rows"][0]["gpu_j"], 123.4, "rows priced after stop() reads the box's telemetry")

    def test_no_meter_leaves_energy_unknown_never_zero(self):
        class NoMeter(FakeMeter):
            source = None
        rc, res, log, out = self.run_order(True, meter=NoMeter)
        self.assertIsNone(res["rows"][0].get("gpu_j"), "a meter that could not measure must not read as free")

    def test_a_model_that_writes_nothing_fails(self):
        rc, res, log, _out = self.run_order(False)
        self.assertEqual(res["solved"], 0)
        self.assertFalse(res["rows"][0]["passed"])
        self.assertGreater(res["rows"][0]["tests_total"], 0, "graded, not 0/0")

    def run_crashing(self, crashes, engine=lambda host: True):
        """The engine drops the connection on the first `crashes` exercise attempts (each attempt's first call).
        `engine` stands in for the live /api/version check - a test never depends on a real server."""
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
        good = scripted(True)
        state = {"attempt_calls": 0, "crashes": 0}

        def flaky(host, model, messages, options, think=False, tools=None, **kw):
            if len(messages) <= 2 and state["crashes"] < crashes:      # an attempt's first model call
                state["crashes"] += 1
                raise ConnectionResetError("[WinError 10054] An existing connection was forcibly closed")
            return good(host, model, messages, options, think=think, tools=tools, **kw)
        buf = io.StringIO()
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", flaky), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), mock.patch.object(O, "engine_up", engine), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", buf):
            O.main()
        return json.load(open(os.path.join(out, "results.json"), encoding="utf-8")), buf.getvalue(), out

    def test_an_engine_crash_is_void_and_runs_once_more(self):
        res, log, out = self.run_crashing(1)
        self.assertEqual((res["solved"], res["void"]), (1, 0), "the rerun measured the model")
        self.assertEqual(len(res["void_rows"]), 1)
        self.assertIn("engine/harness error", res["void_rows"][0]["void"])
        self.assertIn("ALERT: A1 ", log)
        self.assertIn("running it once more", log)
        self.assertTrue(os.path.exists(os.path.join(out, "transcripts", EX + ".void1.json")), "the void attempt kept")

    def test_the_rerun_waits_for_a_dead_engine_to_answer(self):
        """10-08 15:15: the rerun fired 22 s after ollama died and voided again for nothing. It waits on status now."""
        answers = iter([False, False, True])
        polls = []

        def up(host):
            polls.append(host)
            return next(answers)
        res, log, _out = self.run_crashing(1, engine=up)
        self.assertEqual(len(polls), 3, "polled until the engine answered")
        self.assertIn("engine not answering - waiting before the rerun", log)
        self.assertEqual((res["solved"], res["void"]), (1, 0), "then the rerun measured the model")

    def test_a_second_void_is_recorded_never_scored(self):
        res, log, _out = self.run_crashing(2)
        self.assertEqual((res["solved"], res["void"]), (0, 1))
        self.assertIn("VOID twice", log)
        line = [l for l in log.splitlines() if l.startswith("[a1] ")][-1]
        self.assertIn("void 1", line)
        import p20_foundry as PF
        sm = PF.COMPUTE_SOLVED_RE.search(line)
        self.assertEqual((sm.group(1), sm.group(2)), ("0", "0"), "a void row is not in the running score")
        self.assertEqual(PF.COMPUTE_VOID_RE.search(line).group(1), "1", "the factory's REAL parser reads the void count")
        alerts = [PF.COMPUTE_ALERT_RE.match(l.strip()) for l in log.splitlines()]
        alerts = [a.group(1) for a in alerts if a]
        self.assertEqual(len(alerts), 2, "first void, then void twice - each a distinct DM: %s" % alerts)

    def test_a_resume_reruns_void_rows_and_keeps_real_ones(self):
        """a box freeze left a 0/0-graded row and a 0-turn row; a resume must re-run them."""
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
        json.dump({"rows": [{"exercise_dir": EX, "passed": False, "tests_passed": 0, "tests_total": 0, "turns": 43,
                             "ended": "error"}]},
                  open(os.path.join(out, "results.json"), "w"))
        buf = io.StringIO()
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", scripted(True)), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", buf):
            O.main()
        self.assertIn("1 void row(s) run again (%s)" % EX, buf.getvalue())
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertTrue(res["rows"][0]["passed"], "the void row was re-run, not kept")
        self.assertGreater(res["rows"][0]["tests_total"], 0)

    def test_a_resume_keeps_a_real_crash(self):
        """A 0-test row with no loop error is the model's code not importing - a real failure, never re-run."""
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
        crashed = {"exercise_dir": EX, "passed": False, "tests_passed": 0, "tests_total": 0, "turns": 9,
                   "ended": "finish", "graded": "crashed"}
        json.dump({"rows": [crashed]}, open(os.path.join(out, "results.json"), "w"))
        buf = io.StringIO()
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", scripted(True)), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", buf):
            O.main()
        self.assertIn("1 exercise(s) already answered are kept; 0 void", buf.getvalue())
        # the foundry's first progress mark (p20_foundry.COMPUTE_PROG_RE: `[tag] N/T<space>`) at the kept count, so
        # a resumed order's rate is this process's own units
        self.assertRegex(buf.getvalue(), r"(?m)^\[a1\] 1/1 resumed · solved 0/1 · void 0$")
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertEqual(res["rows"][0].get("graded"), "crashed", "the real crash stands")

    def test_an_effort_level_reaches_the_model_as_the_level_string(self):
        """qwen3.8 renders think=true as MEDIUM; an --effort=xhigh plan must send "xhigh", never True."""
        seen, base = [], scripted(True)

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            seen.append(think)
            return base(host, model, messages, options, think=think, tools=tools, **kw)
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX, "think": "xhigh"},
                  open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            O.main()
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"xhigh"})
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertEqual(res["think"], "xhigh")

    def test_a_broken_grader_voids_the_order(self):
        with mock.patch.object(O, "control", lambda ex: (False, "reference 0/0, stub 0/0")):
            rc, res, log, _out = self.run_order(True)
        self.assertEqual(rc, 1)
        self.assertTrue(res["result"].startswith("VOID"))
        self.assertNotIn("rows", res, "no model call after a failed control")


class TeamPlan(unittest.TestCase):
    """A plan with `team` gives the lead the delegate tool; each piece and its wait land on the row."""

    def test_a_directed_team_plan_tells_the_lead_and_an_organic_one_does_not(self):
        import a1_delegate

        class FakeDelegator:
            def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, **kw):
                self.pieces, self.exercise = [], ""

            def __call__(self, brief):
                return "x"
        for mode, expect in (("directed", True), ("mandatory", True), ("organic", False), (None, False)):
            first_user = []

            def chat(host, model, messages, options, think=False, tools=None, **kw):
                if not first_user:
                    first_user.append(next(m["content"] for m in messages if m.get("role") == "user"))
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
                return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                        "eval_duration": 1e9, "done_reason": "stop"}
            out = tempfile.mkdtemp(prefix="a1d-")
            self.addCleanup(shutil.rmtree, out, True)
            plan = os.path.join(out, "plan.json")
            team = {"helper": "bighonker", "helper_model": "qwen3.5:9b", "helper_ctx": 7168}
            if mode:
                team["mode"] = mode
            json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX, "team": team},
                      open(plan, "w"))
            with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                    mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                    mock.patch.object(O.time, "sleep", lambda s: None), \
                    mock.patch.object(a1_delegate, "Delegator", FakeDelegator), \
                    mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
                O.main()
            self.assertEqual(O.H.DIRECTED_BRIEF in first_user[0], expect, "team mode %r" % mode)
            self.assertEqual(O.H.MANDATORY_BRIEF in first_user[0], mode == "mandatory", "team mode %r" % mode)

    def test_a_directed_lead_that_never_delegates_stops_after_three_and_an_organic_one_runs_on(self):
        import a1_delegate
        exs = sorted(e for e in os.listdir(O.EXERCISES) if not e.startswith("."))[:4]

        class FakeDelegator:
            def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, **kw):
                self.pieces, self.exercise = [], ""

            def __call__(self, brief):
                return "x"

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            # one real turn (a turnless row is VOID, not the lead's choice), then finish - never delegates
            wrote = any(m.get("role") == "tool" for m in messages)
            tc = ({"function": {"name": "finish", "arguments": {"note": "done"}}} if wrote else
                  {"function": {"name": "write_file", "arguments": {"path": "scratch.py", "content": "x = 1\n"}}})
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                    "eval_duration": 1e9, "done_reason": "stop"}
        got = {}
        for mode in ("directed", "organic"):
            out = tempfile.mkdtemp(prefix="a1nd-")
            self.addCleanup(shutil.rmtree, out, True)
            plan = os.path.join(out, "plan.json")
            json.dump({"model": "fake", "num_ctx": 4096, "exercises": exs, "control": EX,
                       "team": {"helper": "bighonker", "helper_model": "qwen3:8b", "helper_ctx": 17408, "mode": mode}},
                      open(plan, "w"))
            with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                    mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                    mock.patch.object(O.time, "sleep", lambda s: None), \
                    mock.patch.object(a1_delegate, "Delegator", FakeDelegator), \
                    mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
                O.main()
            got[mode] = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertFalse([r for r in got["directed"]["rows"] if r.get("void")], "the fixture's rows must be valid")
        self.assertEqual(len(got["directed"]["rows"]), 3, "stopped after three exercises with no delegation")
        self.assertIn("measuring the lead alone", got["directed"]["stopped_lead_alone"]["why"])
        self.assertEqual(len(got["organic"]["rows"]), 4, "an organic arm is measuring whether it chooses to delegate")
        self.assertNotIn("stopped_lead_alone", got["organic"])

    def test_a_length_cut_at_the_models_whole_context_is_scored_and_one_below_it_is_void(self):
        got = {}
        for label, prompt_tokens, gen_tokens in (("ceiling", 1000, 3096), ("below", 1000, 2000)):
            calls = []

            def chat(host, model, messages, options, think=False, tools=None, **kw):
                calls.append(1)
                if len(messages) == 2:     # every attempt opens with a call cut by length (a void retry repeats it)
                    tc = {"function": {"name": "write_file", "arguments": {"path": "scratch.py", "content": "x = 1\n"}}}
                    return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]},
                            "prompt_eval_count": prompt_tokens, "eval_count": gen_tokens, "eval_duration": 1e9,
                            "done_reason": "length"}
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
                return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                        "eval_duration": 1e9, "done_reason": "stop"}
            out = tempfile.mkdtemp(prefix="a1c-")
            self.addCleanup(shutil.rmtree, out, True)
            plan = os.path.join(out, "plan.json")
            json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
            with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                    mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                    mock.patch.object(O.time, "sleep", lambda s: None), \
                    mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
                O.main()
            got[label] = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))["rows"]
        ceiling = [r for r in got["ceiling"] if r.get("length_at_ceiling")]
        self.assertTrue(ceiling and not ceiling[0].get("void"), "filled its whole context: scored, not void")
        self.assertTrue(all(r.get("void") for r in got["below"]), "cut below the ceiling: a limit we imposed, void")

    def test_a_finish_gate_plan_tells_the_lead_the_rule(self):
        for gate in (True, False):
            first_user = []

            def chat(host, model, messages, options, think=False, tools=None, **kw):
                if not first_user:
                    first_user.append(next(m["content"] for m in messages if m.get("role") == "user"))
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
                return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                        "eval_duration": 1e9, "done_reason": "stop"}
            out = tempfile.mkdtemp(prefix="a1g-")
            self.addCleanup(shutil.rmtree, out, True)
            plan = os.path.join(out, "plan.json")
            body = {"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX, "mode": "agentic-hai"}
            if gate:
                body["finish_gate"] = True
            json.dump(body, open(plan, "w"))
            with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                    mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                    mock.patch.object(O.time, "sleep", lambda s: None), \
                    mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
                O.main()
            self.assertEqual(O.H.FINISH_GATE_BRIEF in first_user[0], gate, "finish_gate=%r" % gate)

    def test_a_team_plan_delegates_and_records_the_piece(self):
        import a1_delegate
        ex_dir = os.path.join(O.EXERCISES, EX)
        example = open(os.path.join(ex_dir, ".meta", "example.py"), encoding="utf-8").read()
        offered, made = [], []

        class FakeDelegator:
            def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, **kw):
                made.append((team, order_name))
                self.pieces, self.exercise = [], ""

            def __call__(self, brief):
                self.pieces.append({"exercise": self.exercise, "brief_chars": len(brief), "submitted": 10.0,
                                    "done": 12.5, "status": "done"})
                return example

        step = {"n": 0}

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            offered.append([t["function"]["name"] for t in tools or []])
            step["n"] += 1
            if step["n"] == 1:
                tc = {"function": {"name": "delegate", "arguments": {"brief": "write the solution"}}}
            elif step["n"] == 2:
                tc = {"function": {"name": "write_file", "arguments": {"path": EX.replace("-", "_") + ".py",
                                                                       "content": example}}}
            else:
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                    "eval_duration": 1e9, "done_reason": "stop"}
        out = tempfile.mkdtemp(prefix="a1t-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX,
                   "team": {"helper": "bighonker", "helper_model": "qwen3.5:9b", "helper_ctx": 7168}}, open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), mock.patch.object(a1_delegate, "Delegator",
                                                                                     FakeDelegator), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        row = res["rows"][0]
        self.assertEqual(rc, 0)
        self.assertIn("delegate", offered[0])
        self.assertEqual(made[0][0]["helper"], "bighonker")
        self.assertTrue(row["passed"])
        self.assertEqual(row["tool_counts"].get("delegate"), 1)
        self.assertEqual([p["status"] for p in row["delegations"]], ["done"])
        self.assertEqual(row["delegate_wait_s"], 2.5)
        self.assertEqual(res["team"]["helper_model"], "qwen3.5:9b")

    def test_a_window_team_plan_opens_one_window_and_closes_it_with_the_run(self):
        # helper WINDOW: team.window -> WindowDelegator (its own lead id, None when this run is not a
        # queued foundry order), close() on the way out by any route, the window record on the results
        import a1_delegate
        ex_dir = os.path.join(O.EXERCISES, EX)
        example = open(os.path.join(ex_dir, ".meta", "example.py"), encoding="utf-8").read()
        made, closed = [], []

        class FakeWindow:
            def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, **kw):
                made.append(kw)
                self.pieces, self.exercise = [], ""
                self.window = {"order": "a1win-x", "status": "placed"}

            def __call__(self, brief):
                self.pieces.append({"exercise": self.exercise, "item": "d1", "brief_chars": len(brief),
                                    "submitted": 10.0, "done": 11.0, "status": "done", "helper_gpu_j": 3.0})
                return example

            def close(self):
                closed.append(1)
                self.window["status"] = "done"
                return self.window

        step = {"n": 0}

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            step["n"] += 1
            if step["n"] == 1:
                tc = {"function": {"name": "delegate", "arguments": {"brief": "write the solution"}}}
            elif step["n"] == 2:
                tc = {"function": {"name": "write_file", "arguments": {"path": EX.replace("-", "_") + ".py",
                                                                       "content": example}}}
            else:
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                    "eval_duration": 1e9, "done_reason": "stop"}
        out = tempfile.mkdtemp(prefix="a1w-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX,
                   "team": {"helper": "bighonker", "helper_model": "qwen3:8b", "helper_ctx": 17408, "mode": "mandatory",
                            "window": True}}, open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), \
                mock.patch.object(a1_delegate, "WindowDelegator", FakeWindow), \
                mock.patch.object(a1_delegate, "Delegator", lambda *a, **k: self.fail("per-piece delegator built")), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(made, [{"lead_order_id": None}])
        self.assertEqual(closed, [1], "closed once, with the run")
        self.assertEqual(res["window"], {"order": "a1win-x", "status": "done"})
        self.assertEqual(res["rows"][0]["delegations"][0]["item"], "d1")
        self.assertEqual(res["rows"][0]["delegate_wait_s"], 1.0)

    def test_a_review_plan_asks_a_reviewer_delegator_once_and_prices_its_piece(self):
        # A1-review: plan["review"] builds a SECOND Delegator with the a1review- prefix (its own desk
        # orders, never confused with helper pieces); the reviewer's verdict and wait land on the row
        import a1_delegate
        ex_dir = os.path.join(O.EXERCISES, EX)
        example = open(os.path.join(ex_dir, ".meta", "example.py"), encoding="utf-8").read()
        made, asked = [], []

        class FakeDelegator:
            def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, **kw):
                made.append((team["helper_model"], kw.get("prefix", "a1team-")))
                self.pieces, self.exercise = [], ""

            def __call__(self, package):
                asked.append(package)
                self.pieces.append({"exercise": self.exercise, "brief_chars": len(package), "submitted": 10.0,
                                    "done": 13.0, "status": "done"})
                return "NO DEFECTS"

        step = {"n": 0}

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            step["n"] += 1
            if step["n"] == 1:
                tc = {"function": {"name": "write_file", "arguments": {"path": EX.replace("-", "_") + ".py",
                                                                       "content": example}}}
            else:
                tc = {"function": {"name": "finish", "arguments": {"note": "done"}}}
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}, "eval_count": 5,
                    "eval_duration": 1e9, "done_reason": "stop"}
        out = tempfile.mkdtemp(prefix="a1r-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX,
                   "team": {"helper": "bighonker", "helper_model": "qwen3.5:9b", "helper_ctx": 7168},
                   "review": {"helper": "bighonker", "helper_model": "qwen3:8b", "helper_ctx": 17408, "mode": "review"}},
                  open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), mock.patch.object(O.IO, "PowerMeter", FakeMeter), \
                mock.patch.object(O.time, "sleep", lambda s: None), mock.patch.object(a1_delegate, "Delegator",
                                                                                     FakeDelegator), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        row = res["rows"][0]
        self.assertEqual(rc, 0)
        self.assertEqual(made, [("qwen3.5:9b", "a1team-"), ("qwen3:8b", "a1review-")])
        self.assertEqual(len(asked), 1)
        self.assertIn("## The solution files as they stand", asked[0])
        self.assertTrue(row["passed"])
        self.assertEqual((row["review"]["clean"], row["tool_counts"]["review"]), (True, 1))
        self.assertEqual([p["status"] for p in row["review_pieces"]], ["done"])
        self.assertEqual(row["review_wait_s"], 3.0)
        self.assertEqual(row["delegations"], [], "the helper was never asked; the reviewer's piece is not a delegation")
        self.assertEqual(res["review"]["helper_model"], "qwen3:8b")


def _norm(m):
    """What ollama's /api/chat reads of a message: role, content, the calls' names + arguments."""
    calls = [((tc.get("function") or tc).get("name"), (tc.get("function") or tc).get("arguments"))
             for tc in (m.get("tool_calls") or [])]
    return (m.get("role"), m.get("content") or "", calls)


class OneCallPath(unittest.TestCase):
    """the GPU boxes' A1 calls moved onto rigby's call path (Inspect's `lab` provider, foundry backend, with
    Inspect's compaction). The move must not change what ollama receives: the plan's options exactly (incl. sampling
    keys Inspect's GenerateConfig has no name for), num_ctx, the turn cap as num_predict, the effort level verbatim,
    the same tool schemas and the same conversation."""

    def test_ollama_receives_what_the_direct_call_sent(self):
        good, got = scripted(True), []

        def chat(host, model, messages, options, think=False, tools=None, **kw):
            got.append({"host": host, "model": model, "messages": [dict(m) for m in messages],
                        "options": dict(options), "think": think, "tools": tools})
            return good(host, model, messages, options, think=think, tools=tools, **kw)
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        opts = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "repeat_penalty": 1.0}
        json.dump({"model": "fake", "num_ctx": 32768, "exercises": [EX], "control": EX, "mode": "agentic",
                   "think": "xhigh", "options": opts, "turn_cap": 2048}, open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        self.assertEqual(rc, 0)
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertTrue(res["rows"][0]["passed"], res["rows"][0])
        self.assertEqual(res["call_path"], "inspect lab provider (foundry) + compaction")
        conv = json.load(open(os.path.join(out, "transcripts", EX + ".json"), encoding="utf-8"))["conversation"]
        want_tools = json.dumps(O.A.to_ollama_tools(O.H._TOOLS), sort_keys=True)
        self.assertGreaterEqual(len(got), 2)
        for g in got:
            self.assertEqual((g["host"], g["model"]), ("http://127.0.0.1:11434", "fake"))
            self.assertEqual(g["options"], dict(opts, num_ctx=32768, num_predict=2048))
            self.assertEqual(g["think"], "xhigh", "an effort level reaches _chat verbatim (the 10-07 effort sandbag)")
            self.assertEqual(json.dumps(g["tools"], sort_keys=True), want_tools)
            self.assertEqual([_norm(m) for m in g["messages"]], [_norm(m) for m in conv[:len(g["messages"])]])
            for m in g["messages"]:
                self.assertLessEqual(set(m) - {"role", "content", "tool_calls"}, {"tool_name"}, m)


class ToolParseErrors(unittest.TestCase):

    def test_the_order_hands_a_parse_error_back_and_carries_on(self):
        good = scripted(True)
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("ollama: XML syntax error on line 3: element <function> closed by </parameter>")
            return good(*a, **k)
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 4096, "exercises": [EX], "control": EX}, open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", flaky), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertTrue(res["rows"][0]["passed"], res["rows"][0])
        self.assertEqual(res["rows"][0]["tool_counts"].get("invalid_tool_call"), 1)
        self.assertEqual(res["errors"], 0)

    def test_a_turn_cap_cut_reaches_the_harness_as_a_runaway_and_the_row_stays_valid(self):
        """The runaway guard on the GPU path: `turn_cap` goes to ollama as num_predict; a reply cut there with no tool
        call is a runaway (done_reason "runaway", not a capped turn), and the exercise carries on."""
        good, seen = scripted(True), []

        def chat(host, model, messages, options, **k):
            seen.append(dict(options))
            if len(seen) == 1:
                return {"message": {"role": "assistant", "content": "let me think " * 900}, "done_reason": "length",
                        "eval_count": 20480, "prompt_eval_count": 900}
            return good(host, model, messages, options, **k)
        out = tempfile.mkdtemp(prefix="a1o-")
        self.addCleanup(shutil.rmtree, out, True)
        plan = os.path.join(out, "plan.json")
        json.dump({"model": "fake", "num_ctx": 32768, "exercises": [EX], "control": EX, "turn_cap": 20480},
                  open(plan, "w"))
        with mock.patch.object(O.IO, "_assert_resident", lambda *a, **k: {"size_vram": 1, "size": 1}), \
                mock.patch.object(O.IO, "_chat", chat), \
                mock.patch.object(sys, "argv", ["a1_order.py", out, plan]), mock.patch("sys.stdout", io.StringIO()):
            rc = O.main()
        res = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
        row = res["rows"][0]
        self.assertEqual(rc, 0)
        self.assertEqual(res["turn_cap"], 20480)
        self.assertEqual({o.get("num_predict") for o in seen}, {20480}, "every call carries the cap")
        self.assertEqual(row["tool_counts"].get("runaway_cut"), 1)
        self.assertEqual(row["capped_turns"], [], "a guard cut is not a limit hit (a1_validity)")
        self.assertTrue(row["passed"], row)
        self.assertIsNone(O.V.verdict(row))

    def test_only_ollamas_tool_call_parse_errors_are_handed_back(self):
        self.assertTrue(O._is_tool_parse_error(RuntimeError("ollama: XML syntax error on line 3: element <function>")))
        self.assertTrue(O._is_tool_parse_error(RuntimeError("ollama: error parsing tool call: raw='{bad'")))
        self.assertFalse(O._is_tool_parse_error(RuntimeError("ollama: model requires more system memory")))
        self.assertFalse(O._is_tool_parse_error(RuntimeError("connection refused")))


class GraderUnderAPathLockedPython(unittest.TestCase):
    """`python -I` drops the cwd and PYTHONPATH from sys.path, as an embeddable Python's `._pth` file does."""

    def test_the_decoy_reproduces_the_lock(self):
        import subprocess
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        open(os.path.join(d, "lockprobe_test.py"), "w").write("import unittest\n")
        p = subprocess.run([sys.executable, "-I", "-m", "unittest", "lockprobe_test"], cwd=d, capture_output=True,
                           text=True, env={**os.environ, "PYTHONPATH": d})
        self.assertIn("No module named 'lockprobe_test'", p.stdout + p.stderr)

    def test_the_control_passes_under_the_lock(self):
        with mock.patch.object(O.H, "PY", [sys.executable, "-I"]):
            ok, detail = O.control(os.path.join(O.EXERCISES, EX))
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
