"""Resumption invariants: completed model calls replay without re-execution; in-flight calls resume from the current turn; recorded tool results are never re-executed; calls in re-asked turns are not served stale answers; runs with no checkpoints are stopped rather than looped; and all lanes resume with the same run ID."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import cancellation  # noqa: E402
import history  # noqa: E402
import live_runs  # noqa: E402
import ollama_client  # noqa: E402
import run_ledger  # noqa: E402


class _Crash(BaseException):
    """Stands in for the process dying mid-run (BaseException: no handler in the loop may swallow it)."""


class _TmpRoot(unittest.TestCase):
    def setUp(self):
        # _persist on a chat's first exchange starts the auto-titler, a REAL chat() to BigHonker (see
        # test_live_streaming._HistoryTmp).
        p = patch.object(app, "_auto_title_worker", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self._root = run_ledger.ROOT
        run_ledger.ROOT = pathlib.Path(self.tmp) / "hai-runs"
        self._hist = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self.tmp) / "history"
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()
        cancellation.unbind()

    def tearDown(self):
        run_ledger.ROOT = self._root
        history.HISTORY_DIR = self._hist
        shutil.rmtree(self.tmp, ignore_errors=True)
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()


class LedgerTests(_TmpRoot):
    def test_keys_record_and_finish(self):
        led = run_ledger.RunLedger.create("r1", "stream", {"q": {"text": "x"}})
        a, b = led.open_call(), led.open_call()
        self.assertEqual((a.key, b.key), ("0", "1"))
        a.save([{"role": "user", "content": "x"}], 3)
        self.assertEqual(a.child_key(), "0.t3.0")
        self.assertEqual(a.child_key(), "0.t3.1")
        a.done(("ans", None))
        self.assertEqual(run_ledger.RunLedger("r1").open_call().recorded(), ("ans", None))
        b.done((None, ["mothership", "why"]))
        self.assertEqual(b.recorded(), (None, ("mothership", "why")), "an escalation round-trips as a tuple")
        self.assertEqual([i["run_id"] for _l, i in run_ledger.interrupted()], ["r1"])
        led.finish()
        self.assertEqual(run_ledger.interrupted(), [])

    def test_resume_drops_the_children_of_the_turn_in_flight(self):
        led = run_ledger.RunLedger.create("r2", "stream", {})
        outer = led.open_call()
        outer.save(["turn1"], 1)
        kept = led.open_call(outer)                  # 0.t1.0: a finished turn's child
        kept.done(("child of turn 1", None))
        outer.save(["turn1", "turn2"], 2)
        lost = led.open_call(outer)                  # 0.t2.0: made inside the turn that was in flight
        lost.done(("child of turn 2", None))
        point = run_ledger.RunLedger("r2").open_call().resume_point()
        self.assertEqual(point, {"messages": ["turn1", "turn2"], "step": 2, "tools_done": None, "in_tool": False,
                                 "compact": False, "cut": None, "loop": None, "pieces": {}, "handle": None})
        self.assertTrue((led.dir / "calls" / "0.t1.0.json").exists())
        self.assertFalse((led.dir / "calls" / "0.t2.0.json").exists(),
                         "a re-asked turn may request different work; it must not be served this answer")

    def test_a_finished_run_becomes_a_rolling_save_with_its_whole_conversation(self):
        """Saves preserve the full conversation for each call, not just the final answer, to support resuming from any internal turn."""
        with patch.object(run_ledger.config, "RUN_SAVES_KEEP", 2):
            for n in range(3):
                led = run_ledger.RunLedger.create("s%d" % n, "stream", {"q": {"text": "x%d" % n}})
                s = led.open_call()
                s.save([{"role": "user", "content": "x%d" % n}, {"role": "tool", "content": "r"}], 2)
                s.done(("answer %d" % n, None))
                led.finish()
                time.sleep(0.002)
        self.assertEqual(run_ledger.interrupted(), [], "a saved run is not a run in flight")
        saves = run_ledger.saved()
        self.assertEqual([r["run_id"] for r in saves], ["s2", "s1"], "the newest two kept, the oldest rotated out")
        rec = run_ledger.read_save(saves[0]["save"])
        self.assertEqual(rec["calls"]["0"]["out"], ["answer 2", None])
        self.assertEqual(rec["calls"]["0"]["messages"][-1], {"role": "tool", "content": "r"})
        self.assertIsNone(run_ledger.read_save(".."), "a save name can never walk out of the saves")

    def test_a_pinned_run_is_kept_past_the_rolling_window(self):
        with patch.object(run_ledger.config, "RUN_SAVES_KEEP", 1):
            keep = run_ledger.RunLedger.create("pin-me", "stream", {})
            self.assertTrue(keep.pin())
            keep.finish()
            for n in range(2):
                run_ledger.RunLedger.create("auto%d" % n, "stream", {}).finish()
        ids = {r["run_id"]: r["pinned"] for r in run_ledger.saved()}
        self.assertEqual(ids, {"pin-me": True, "auto1": False})

    def test_a_run_a_guard_stopped_stays_resumable_and_restores_from_its_checkpoint(self):
        """Runs stopped by a guard rule must remain resumable and not be rotated out by save limits."""
        with patch.object(run_ledger.config, "RUN_SAVES_KEEP", 1):
            led = run_ledger.RunLedger.create("stopped-1", "stream", {})
            s = led.open_call()
            s.save([{"role": "user", "content": "go"}, {"role": "tool", "content": "r"}], 7, loop={"progress": {"idle_turns": 3}})
            s.stopped = "idle"
            s.done(("handoff", None))
            led.finish()
            for n in range(3):                                     # later finished runs do not rotate it out
                later = run_ledger.RunLedger.create("later-%d" % n, "stream", {})
                later.open_call().done(["an answer", None])
                later.finish()
        self.assertIn("stopped-1", [r["run_id"] for r in run_ledger.saved()], "kept like an answerless run")
        live, name = run_ledger.restore_save("stopped-1")
        self.assertIsNotNone(live, name)
        top = live.open_call()                                     # call "0", the run's own loop
        self.assertIsNone(top.recorded(), "the handoff is set aside: the call is live again")
        point = top.resume_point()
        self.assertEqual((point["step"], point["loop"]), (7, {"progress": {"idle_turns": 3}}), "from its last checkpoint")

    def test_a_run_that_finished_its_answer_is_still_not_resumable(self):
        led = run_ledger.RunLedger.create("finished-1", "stream", {})
        s = led.open_call()
        s.save([{"role": "user", "content": "go"}], 2)
        s.done(("the answer", None))
        led.finish()
        self.assertEqual(run_ledger.restore_save("finished-1")[0], None)

    def test_rotated_saves_are_archived_not_deleted_and_can_be_found(self):
        with patch.object(run_ledger.config, "RUN_SAVES_KEEP", 1):
            for n in range(2):
                led = run_ledger.RunLedger.create("roll-%d" % n, "stream", {})
                led.open_call().done(["an answer", None])
                led.finish()
                time.sleep(0.002)
        arch = run_ledger.archive_root(run_ledger.saves_root(run_ledger.ROOT))
        self.assertTrue(any(d.name.startswith("roll-0.") for d in arch.iterdir()), "the rotated save is in the archive")
        self.assertEqual([r["run_id"] for r in run_ledger.saved()], ["roll-1"])

    def test_keep_none_removes_the_ledger_as_before(self):
        with patch.object(run_ledger.config, "RUN_SAVES_KEEP", 0):
            run_ledger.RunLedger.create("gone", "stream", {}).finish()
        self.assertEqual(run_ledger.saved(), [])

    def test_save_endpoints_are_admin_only_and_pin_a_run_in_flight(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        run_ledger.RunLedger.create("live-1", "stream", {})
        self.assertEqual(c.post("/runs/save", json={"user": "NotAnAdmin", "run_id": "live-1"}).status_code, 403)
        self.assertEqual(c.get("/runs/saved", params={"user": "NotAnAdmin"}).status_code, 403)
        admin = sorted(app.config.ADMIN_USERS)[0]
        self.assertEqual(c.post("/runs/save", json={"user": admin, "run_id": "nope"}).status_code, 404)
        self.assertTrue(c.post("/runs/save", json={"user": admin, "run_id": "live-1"}).json()["pinned"])
        self.assertTrue(run_ledger.RunLedger("live-1").info().get("pinned"))

    def test_progress_mark_is_stable_and_moves_with_a_checkpoint(self):
        led = run_ledger.RunLedger.create("r3", "stream", {})
        s = led.open_call()
        s.save(["a"], 1)
        m1 = led.progress_mark()
        self.assertEqual(m1, run_ledger.RunLedger("r3").progress_mark(), "comparable across instances/processes")
        time.sleep(0.01)
        s.save(["a", "b"], 2)
        self.assertNotEqual(m1, led.progress_mark())


SUMMARISER = "You maintain durable conversation memory for Doc. Summarise the work so far."
AGENT = "You are an agent working a job. Use the tools."
ONE_TOOL = [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}]


class ATopLevelCallIsMatchedToItsOwnRecord(_TmpRoot):
    """A resumed top-level chat() call gets its OWN saved record, never another kind of call's. A call carries a
    signature (the first line of its system prompt + its sorted tool names) and is matched to the first record not
    yet handed out with that signature, so a call that does not recur on resume (a compaction that already ran)
    cannot shift the others onto the wrong record. Records without a signature keep positional matching."""

    SUMMARISER = SUMMARISER
    AGENT = AGENT
    NO_TOOLS = []
    ONE_TOOL = ONE_TOOL

    def _call(self, led, system, tools, script):
        """One real chat() loop under a ledger, with the model and the tools faked."""
        seq = list(script)

        def fake_once(host, model, messages, *a, **k):
            self.asks.append(list(messages))
            nxt = seq.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt

        def fake_tool(fn, args, *a, **k):
            self.tools.append(args.get("q"))
            return "result for %s" % args.get("q")

        with patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", fake_tool), \
                patch.object(ollama_client.tool_selection, "select",
                             side_effect=lambda tl, *_a, **_k: (tl, None)), \
                patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.run_ledger(led), ollama_client.call_context(job=True):
            return ollama_client.chat("h", "m", [{"role": "system", "content": system},
                                                 {"role": "user", "content": "THE BRIEF"}], list(tools))

    @staticmethod
    def _tool_turn(tag):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "search_docs", "arguments": {"q": tag}}}]}, "done_reason": "stop"}

    def setUp(self):
        super().setUp()
        self.asks, self.tools = [], []

    def test_the_incident_a_resumed_tool_loop_gets_its_own_checkpoint_not_the_summary(self):
        led = run_ledger.RunLedger.create("inc1", "stream", {})
        # run 1, call 0: the compaction. No tools, a system prompt of its own. It finished.
        self.assertEqual(self._call(led, SUMMARISER, [],
                                   [{"message": {"role": "assistant", "content": "COMPACTION SUMMARY"},
                                    "done_reason": "stop"}]), ("COMPACTION SUMMARY", None))
        # run 1, call 1: the job's tool loop, cut mid-turn by the restart.
        with self.assertRaises(_Crash):
            self._call(led, AGENT, ONE_TOOL, [self._tool_turn("a"), _Crash()])
        self.assertEqual(sorted(f.stem for f in (led.dir / "calls").glob("*.json")), ["0", "1"])

        # the resumed process: the compaction already ran, so the tool loop is the FIRST call it opens.
        resumed = run_ledger.RunLedger("inc1")
        slot = resumed.open_call(sig=ollama_client._call_sig([{"role": "system", "content": AGENT}], ONE_TOOL))
        self.assertEqual(slot.key, "1", "the tool loop is matched to its own record, not to position 0")
        self.assertIsNone(slot.recorded(), "never another call's answer")
        point = slot.resume_point()
        self.assertEqual((point["step"], point["tools_done"]), (2, None),
                         "the loop's own checkpoint: its first turn and its tool result are saved")
        self.assertTrue(any("result for a" in str(m.get("content") or "") for m in point["messages"]),
                        "the resume point is the loop's conversation, not the compaction's")
        answer, _ = self._call(resumed, AGENT, ONE_TOOL,
                               [{"message": {"role": "assistant", "content": "the finished answer"},
                                "done_reason": "stop"}])
        self.assertEqual(answer, "the finished answer")
        self.assertFalse(any("COMPACTION SUMMARY" in str(m.get("content") or "") for m in self.asks[-1]),
                        "the compaction's saved answer is never handed to the tool loop")

    def test_the_same_sequence_on_both_runs_replays_the_finished_call_and_resumes_the_other(self):
        led = run_ledger.RunLedger.create("inc2", "stream", {})
        self._call(led, SUMMARISER, [],
                   [{"message": {"role": "assistant", "content": "COMPACTION SUMMARY"}, "done_reason": "stop"}])
        with self.assertRaises(_Crash):
            self._call(led, AGENT, ONE_TOOL, [self._tool_turn("a"), _Crash()])
        resumed = run_ledger.RunLedger("inc2")
        asks_before = len(self.asks)
        self.assertEqual(resumed.open_call(sig=ollama_client._call_sig([{"role": "system", "content": SUMMARISER}], [])).recorded(),
                         ("COMPACTION SUMMARY", None), "a call that already finished replays without a model call")
        self.assertEqual(len(self.asks), asks_before, "replayed: no model call spent")
        slot = resumed.open_call(sig=ollama_client._call_sig([{"role": "system", "content": AGENT}], ONE_TOOL))
        self.assertEqual((slot.key, slot.recorded()), ("1", None), "today's positional result, by signature too")
        point = slot.resume_point()
        self.assertEqual((point["step"], point["tools_done"]), (2, None), "the loop's own checkpoint")
        self.assertTrue(any("result for a" in str(m.get("content") or "") for m in point["messages"]))

    def test_a_call_whose_signature_matches_nothing_gets_a_fresh_key_that_touches_no_other_record(self):
        led = run_ledger.RunLedger.create("fresh1", "stream", {})
        sig_a = ollama_client._call_sig([{"role": "system", "content": SUMMARISER}], [])
        sig_b = ollama_client._call_sig([{"role": "system", "content": AGENT}], ONE_TOOL)
        a, b = led.open_call(sig=sig_a), led.open_call(sig=sig_b)
        a.save([{"role": "user", "content": "x"}], 1)
        a.done(("answer A", None))
        b.save([{"role": "user", "content": "y"}], 2)
        b.done(("answer B", None))
        before = {f.name: f.read_text(encoding="utf-8") for f in (led.dir / "calls").glob("*.json")}
        sig_c = ollama_client._call_sig([{"role": "system", "content": "You are something else entirely."}], [])
        c = run_ledger.RunLedger("fresh1").open_call(sig=sig_c)
        self.assertEqual(c.key, "2", "one past every existing top-level key, so it cannot overwrite a saved call")
        c.save([{"role": "user", "content": "z"}], 1)
        c.done(("answer C", None))
        for name, text in before.items():
            self.assertEqual((led.dir / "calls" / name).read_text(encoding="utf-8"), text,
                             "the new call wrote its own record only")
        self.assertEqual(json.loads((led.dir / "calls" / "2.json").read_text(encoding="utf-8"))["sig"], sig_c)

    def test_a_record_written_before_signatures_still_resumes_positionally(self):
        led = run_ledger.RunLedger.create("pre1", "stream", {})
        old = led.open_call()                       # no sig: exactly how calls were recorded before this change
        old.save([{"role": "user", "content": "go"}], 5)
        slot = run_ledger.RunLedger("pre1").open_call(
            sig=ollama_client._call_sig([{"role": "system", "content": "anything at all"}], ONE_TOOL))
        self.assertEqual(slot.key, "0", "an unmarked record is still the one a call lands on")
        self.assertEqual(slot.resume_point()["step"], 5)
        self.assertIsNone(slot.recorded())

    def test_the_signature_ignores_the_box_the_model_and_everything_after_the_first_system_line(self):
        sig = ollama_client._call_sig([{"role": "system", "content": "FIRST LINE\nsecond line of the prompt"}], [])
        self.assertEqual(sig, ollama_client._call_sig(
            [{"role": "system", "content": "FIRST LINE\nan entirely different tail"},
             {"role": "user", "content": "a long conversation"},
             {"role": "assistant", "content": "more of it"}], []),
            "a resume may land on another box and the conversation grows; the sig must survive both")
        self.assertNotEqual(sig, ollama_client._call_sig(
            [{"role": "system", "content": "SECOND FIRST LINE"}], []), "a different kind of call is a different sig")
        one = [{"type": "function", "function": {"name": "coding_patch", "parameters": {}}}]
        two = [{"type": "function", "function": {"name": "coding_test", "parameters": {}}},
               {"type": "function", "function": {"name": "coding_patch", "parameters": {}}}]
        self.assertEqual(ollama_client._call_sig([], two), ollama_client._call_sig([], list(reversed(two))),
                         "the tool names are sorted, so order does not matter")
        self.assertNotEqual(ollama_client._call_sig([], one), ollama_client._call_sig([], two))
        self.assertEqual(len(sig), 16)


class ACompactionARestartCutIsRedone(_TmpRoot):
    """Verify that resuming during an in-flight compaction is lossless.\n\n    Without this, the checkpoint taken just before the compaction lacks the turn\n    being compacted, so a restart mid-compaction would lose that turn's output.\n"""

    CTX = 10000
    TOOLS = [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}]

    def _run(self, led, script, crash_in_summary):
        seq = list(script)

        def fake_once(host, model, messages, *a, **k):
            if messages and str(messages[0].get("content") or "").startswith("You are compacting"):
                self.summaries.append(messages)
                if crash_in_summary:
                    raise _Crash()
                return {"message": {"role": "assistant", "content": "SUMMARY OF THE WORK"}}
            self.calls.append(list(messages))
            return seq.pop(0)

        def fake_tool(fn, args, *a, **k):
            self.tool_runs.append(args.get("q"))
            return "result for %s" % args.get("q")
        with patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                patch.object(ollama_client.tools, "run_tool", side_effect=fake_tool), \
                patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.run_ledger(led), ollama_client.call_context(job=True):
            return ollama_client.chat("h", "m", [{"role": "system", "content": "SYS"}, {"role": "user", "content": "THE BRIEF"}],
                                      list(self.TOOLS), num_ctx=self.CTX)

    def setUp(self):
        super().setUp()
        self.calls, self.summaries, self.tool_runs = [], [], []

    @staticmethod
    def _tool_turn(prompt_tokens, tag):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "search_docs", "arguments": {"q": tag}}}]},
            "done_reason": "stop", "prompt_eval_count": prompt_tokens, "eval_count": 50}

    def test_a_restart_inside_an_ordinary_compaction_keeps_the_last_tool_result(self):
        script = [self._tool_turn(1000, "early"), self._tool_turn(1000, "middle"), self._tool_turn(int(0.95 * self.CTX), "late")]
        led = run_ledger.RunLedger.create("cc1", "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, script, crash_in_summary=True)
        self.assertEqual(self.tool_runs, ["early", "middle", "late"], "fixture: it died in the summary, after every tool")
        answer, _ = self._run(run_ledger.RunLedger("cc1"), [{"message": {"role": "assistant", "content": "done"},
                                                               "done_reason": "stop", "prompt_eval_count": 100}], False)
        self.assertEqual(answer, "done")
        self.assertEqual(self.tool_runs, ["early", "middle", "late"], "no tool re-run")
        last = [str(m.get("content") or "") for m in self.calls[-1]]
        self.assertTrue(any("result for late" in t for t in last), "the last step's result survived the restart")
        self.assertFalse(any("interrupted by an orchestrator restart" in t for t in last))
        self.assertTrue(any("SUMMARY OF THE WORK" in t for t in last), "the compaction was redone")
        self.assertEqual(len(self.summaries), 2, "one cut by the restart, one redone")

    def test_a_restart_inside_a_cut_turns_summary_keeps_the_thought(self):
        cut = {"message": {"role": "assistant", "content": "", "thinking": "half a thought"}, "done_reason": "length",
               "prompt_eval_count": 6000, "eval_count": 4000}
        led = run_ledger.RunLedger.create("cc2", "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, [self._tool_turn(1000, "early"), self._tool_turn(1000, "late"), cut], crash_in_summary=True)
        asked = len(self.calls)
        answer, _ = self._run(run_ledger.RunLedger("cc2"), [{"message": {"role": "assistant", "content": "done"},
                                                               "done_reason": "stop", "prompt_eval_count": 100}], False)
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 2, "the summary the restart cut was redone (the crashed one is the first)")
        self.assertIn("half a thought", self.summaries[-1][1]["content"], "the resumed summary still has the cut thought")
        self.assertEqual(len(self.calls), asked + 1, "the cut turn was not asked again from scratch: summary, then one turn")


class TheFinishGateSurvivesARestart(_TmpRoot):
    """Verify that the finish gate state persists across restarts, preventing a resumed job from shipping unverified changes as finished."""

    TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("coding_patch", "coding_test")]

    def _run(self, led, script):
        seq = list(script)

        def fake_once(host, model, messages, *a, **k):
            self.calls.append(list(messages))
            nxt = seq.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt
        with patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                patch.object(ollama_client.tools, "run_tool", side_effect=lambda fn, args, *a, **k: "ok"), \
                patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.run_ledger(led), ollama_client.call_context(job=True):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "fix the bug"}], list(self.TOOLS), num_ctx=100000)

    def test_the_idle_detector_and_the_counts_round_trip(self):
        p = ollama_client._LoopProgress()
        p.call("search_docs", {"q": "a"}, ok=True)
        p.end_turn(False)
        p.resets = 1
        q = ollama_client._LoopProgress.restore(json.loads(json.dumps(p.state())))
        self.assertEqual((q.version, q.idle_turns, q.resets), (p.version, p.idle_turns, p.resets),
                         "a restart must not hand a stuck job a second reset")
        self.assertFalse(q.call("search_docs", {"q": "a"}, ok=True), "a call made before the restart is still a repeat")

    def test_the_stall_rule_knows_its_states_across_a_restart(self):
        p = ollama_client._LoopProgress()
        for old, sha in (("x", "A"), ("y", "B"), ("z", "A")):
            p.call("coding_patch", {"path": "a.py", "old": old}, ok=True, result={"new_sha256": sha})
            p.end_turn(True)
        self.assertEqual(p.stall_turns, 1, "fixture: back on version A is a stall turn")
        q = ollama_client._LoopProgress.restore(json.loads(json.dumps(p.state())))
        self.assertEqual((q.files, q.states, q.looked, q.stall_turns), (p.files, p.states, p.looked, p.stall_turns))
        q.call("coding_patch", {"path": "a.py", "old": "w"}, ok=True, result={"new_sha256": "B"})
        q.end_turn(True)
        self.assertEqual(q.stall_turns, 2, "the states before the restart are still known after it")

    def test_a_resumed_job_that_edited_code_is_still_held_to_its_tests(self):
        self.calls = []
        edit = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "coding_patch", "arguments": {"path": "a.py", "patch": "x"}}}]}, "done_reason": "stop"}
        led = run_ledger.RunLedger.create("gate1", "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, [edit, _Crash()])
        resumed_from = len(self.calls)
        done = {"message": {"role": "assistant", "content": "done, all fixed"}, "done_reason": "stop"}
        self._run(run_ledger.RunLedger("gate1"), [dict(done) for _ in range(ollama_client.config.CODING_IDLE_TURNS + 1)])
        after = self.calls[resumed_from:]
        self.assertGreater(len(after), 1, "the first answer after the restart was refused: the gate remembered the edit")
        self.assertTrue(any(m.get("content") == ollama_client._GATE_REFUSAL for m in after[1]))


class ASummaryInPiecesResumesAtThePieceInFlight(_TmpRoot):
    """A conversation too big for one summary request goes in pieces. A restart during piece 2 must not send piece 1 again:
    its summary is in the checkpoint, so the first request after the restart is piece 2, the one the window can reconnect."""

    CTX = 10000
    TOOLS = [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}]

    def _run(self, led, script, crash_on_summary):
        seq = list(script)

        def fake_once(host, model, messages, *a, **k):
            if messages and str(messages[0].get("content") or "").startswith("You are compacting"):
                self.summaries.append(messages[1]["content"])
                if len(self.summaries) == crash_on_summary:
                    raise _Crash()
                return {"message": {"role": "assistant", "content": "S%d" % len(self.summaries)}}
            return seq.pop(0)
        with patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                patch.object(ollama_client.tools, "run_tool",
                             side_effect=lambda fn, args, *a, **k: ("result for %s " % args.get("q")) + "x" * 9000), \
                patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.run_ledger(led), ollama_client.call_context(job=True):
            return ollama_client.chat("h", "m", [{"role": "system", "content": "SYS"}, {"role": "user", "content": "THE BRIEF"}],
                                      list(self.TOOLS), num_ctx=self.CTX)

    @staticmethod
    def _tool_turn(prompt_tokens, tag):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "search_docs", "arguments": {"q": tag}}}]},
            "done_reason": "stop", "prompt_eval_count": prompt_tokens, "eval_count": 50}

    def test_a_restart_on_piece_two_does_not_send_piece_one_again(self):
        self.summaries = []
        script = [self._tool_turn(1000, "a"), self._tool_turn(1000, "b"), self._tool_turn(1000, "c"),
                  self._tool_turn(int(0.95 * self.CTX), "d")]
        led = run_ledger.RunLedger.create("pc1", "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, script, crash_on_summary=2)
        self.assertEqual(len(self.summaries), 2, "fixture: the summary went in pieces and died on the second")
        first, second = self.summaries
        answer, _ = self._run(run_ledger.RunLedger("pc1"), [{"message": {"role": "assistant", "content": "done"},
                                                               "done_reason": "stop", "prompt_eval_count": 100}], 0)
        self.assertEqual(answer, "done")
        resumed = self.summaries[2:]
        self.assertTrue(resumed, "the summary was finished after the restart")
        self.assertEqual(resumed[0], second, "the first request after the restart is the piece that was in flight")
        self.assertNotIn(first, resumed, "a finished piece is never sent again")


class ARestartInsideAToolLosesNothing(_TmpRoot):

    def _run(self, led, tool, crash):
        self.runs = getattr(self, "runs", [])

        def fake_once(host, model, messages, *a, **k):
            self.last = list(messages)
            if any(m.get("role") == "tool" for m in messages):
                return {"message": {"role": "assistant", "content": "final answer"}}
            return {"message": {"role": "assistant", "tool_calls": [{"function": {"name": tool, "arguments": {"q": "x"}}}]}}

        def fake_tool(fn, args, *a, **k):
            self.runs.append((fn, None))
            if crash:
                ollama_client.note_tool_handle({"kind": "gpu_page", "job": "J1"})
                raise _Crash()
            return "FRESH RESULT"

        def fake_resume(fn, args, handle):
            self.runs.append((fn, handle))
            return "REATTACHED"
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", fake_tool), \
                patch.object(ollama_client.tools, "resume_tool", fake_resume), ollama_client.run_ledger(led):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}], [{"function": {"name": tool}}])

    def _twice(self, tool, key):
        led = run_ledger.RunLedger.create(key, "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, tool, crash=True)
        answer, _ = self._run(run_ledger.RunLedger(key), tool, crash=False)
        self.assertEqual(answer, "final answer")
        return [str(m.get("content")) for m in self.last]

    def test_a_render_waits_for_the_order_it_placed(self):
        text = self._twice("coding_render", "rt1")
        self.assertEqual(self.runs[-1], ("coding_render", {"kind": "gpu_page", "job": "J1"}), "the same order, not a new one")
        self.assertIn("REATTACHED", text)
        self.assertFalse(any("interrupted by an orchestrator restart" in t for t in text))

    def test_a_test_run_cut_by_a_restart_runs_again(self):
        self.assertIn("coding_test", ollama_client.REPLAYABLE_TOOLS, "the finish gate needs a real verdict, not a lost one")

    def test_a_read_only_tool_runs_again(self):
        text = self._twice("coding_git", "rt2")
        self.assertEqual([r[0] for r in self.runs], ["coding_git", "coding_git"])
        self.assertIn("FRESH RESULT", text)
        self.assertFalse(any("interrupted by an orchestrator restart" in t for t in text))

    def test_a_writing_tool_is_checked_not_run_blind(self):
        text = self._twice("coding_patch", "rt3")
        self.assertEqual(self.runs[-1][0], "coding_patch")
        self.assertIn("REATTACHED", text, "it went to resume_tool, which reads its effect back")
        self.assertFalse(any("interrupted by an orchestrator restart" in t for t in text))

    def test_a_tool_whose_effect_cannot_be_read_back_is_still_never_run_blind(self):
        text = self._twice("coding_task", "rt4")
        self.assertEqual([r[0] for r in self.runs], ["coding_task"], "its effect is unknown: not re-run")
        self.assertTrue(any("coding_task was interrupted by an orchestrator restart" in t for t in text))


class ARestartedRunSendsTheSameBytes(_TmpRoot):

    def test_the_request_after_a_restart_is_the_request_that_was_cut(self):
        sent = []
        now = [1000.0]

        def fake_once(host, model, messages, *a, **k):
            sent.append(json.dumps(messages, sort_keys=True))
            if len(sent) == 2:
                raise _Crash()                # the process dies while the second turn is being written
            if len(sent) >= 3:
                return {"message": {"role": "assistant", "content": "done"}, "done_reason": "stop"}
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "search_docs", "arguments": {"q": "a"}}}]}, "done_reason": "stop"}

        def run(led):
            with patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                    patch.object(ollama_client.tools, "run_tool", side_effect=lambda *a, **k: "result"), \
                    patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                    patch.object(ollama_client.time, "monotonic", lambda: now[0]), \
                    ollama_client.run_ledger(led), \
                    ollama_client.call_context(job=True, loop_deadline=now[0] + 300, loop_budget=300):
                return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                          [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}])
        led = run_ledger.RunLedger.create("cb1", "stream", {})
        with self.assertRaises(_Crash):
            run(led)
        self.assertIn("[Clock: ", sent[1], "fixture: the cut request carried a clock line")
        now[0] += 120                         # two minutes pass before the run comes back
        run(run_ledger.RunLedger("cb1"))
        self.assertEqual(sent[2], sent[1], "the same bytes, so the window reconnects the turn it was writing")


class ChatReplayTests(_TmpRoot):
    """The real chat() loop under a ledger, with the model and the tools faked."""

    def _run(self, led, crash_on_tool=None):
        self.model_calls = getattr(self, "model_calls", 0)
        self.tool_runs = getattr(self, "tool_runs", [])

        def fake_once(host, model, messages, *a, **k):
            self.model_calls += 1
            self.last_messages = list(messages)
            done_tools = sum(1 for m in messages if m.get("role") == "tool")
            if done_tools >= 3:
                return {"message": {"role": "assistant", "content": "final answer"}}
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "write_note", "arguments": {"q": "n%d" % done_tools}}}]}}

        def fake_tool(fn, args, *a, **k):
            if crash_on_tool is not None and args.get("q") == crash_on_tool:
                raise _Crash()
            self.tool_runs.append(args.get("q"))
            return "result for %s" % args.get("q")
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", fake_tool):
            with ollama_client.run_ledger(led):
                return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                          [{"function": {"name": "write_note"}}])

    def test_a_crash_mid_tool_resumes_after_it_and_never_reruns_a_tool(self):
        """A tool that CHANGES things (write_note stands in) is never re-run blind; one that only reads runs again
        (ARestartInsideAToolLosesNothing)."""
        led = run_ledger.RunLedger.create("c1", "stream", {})
        with self.assertRaises(_Crash):
            self._run(led, crash_on_tool="n2")            # dies while running the third tool
        self.assertEqual(self.tool_runs, ["n0", "n1"])
        self.assertEqual(self.model_calls, 3)
        answer, esc = self._run(run_ledger.RunLedger("c1"))  # the restarted process: a fresh ledger object
        self.assertEqual(answer, "final answer")
        self.assertEqual(self.tool_runs, ["n0", "n1"],
                         "nothing re-executed: n0/n1 are recorded, n2 was cut off and its effect is unknown")
        self.assertEqual(self.model_calls, 4, "the turn that chose n2 was NOT re-asked; only the next turn ran")
        self.assertIn("write_note was interrupted by an orchestrator restart", self.last_messages[-1]["content"])
        before = self.model_calls
        again = self._run(run_ledger.RunLedger("c1"))     # a finished call replays with no model call
        self.assertEqual(again, ("final answer", None))
        self.assertEqual(self.model_calls, before)

    def test_park_holds_the_run_at_a_checkpoint_and_release_lets_it_finish(self):
        """Checkpointing must occur only at natural boundaries (between messages or tool calls), never mid-message."""
        led = run_ledger.RunLedger.create("c3", "stream", {})
        out = {}
        self.model_calls, self.tool_runs = 0, []
        ollama_client.park_runs(True)
        self.addCleanup(ollama_client.park_runs, False)
        with patch.object(ollama_client, "_PARK_WAKE", threading.Event()) as wake:
            t = threading.Thread(target=lambda: out.setdefault("r", self._run(led)), daemon=True)
            t.start()
            for _ in range(200):
                if ollama_client.parked_count() == 1:
                    break
                time.sleep(0.01)
            self.assertEqual(ollama_client.parked_count(), 1, "the run holds at its first checkpoint")
            self.assertEqual(self.model_calls, 0, "parked BEFORE the model turn: nothing half-written")
            view = app.runs_parked()
            self.assertEqual((view["running"], view["parked"], view["all_parked"]), (1, 1, True))
            self.assertEqual(len(view["running_ids"]), 1, "the run in flight is named, so a caller can suspend it by id")
            ollama_client.park_runs(False)
            wake.set()
            t.join(5)
        self.assertEqual(out["r"], ("final answer", None))
        self.assertEqual(ollama_client.parked_count(), 0)

    def test_a_crash_between_two_tools_runs_the_rest_without_re_asking_the_model(self):
        """Resume executes tools that were requested but not yet started; it does not re-query the model for the choice."""
        self.model_calls, self.tool_runs = 0, []

        def fake_once(host, model, messages, *a, **k):
            self.model_calls += 1
            if any(m.get("role") == "tool" for m in messages):
                return {"message": {"role": "assistant", "content": "final answer"}}
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "search_docs", "arguments": {"q": "a"}}},
                {"function": {"name": "search_docs", "arguments": {"q": "b"}}}]}}

        def fake_tool(fn, args, *a, **k):
            self.tool_runs.append(args.get("q"))
            return "result for %s" % args.get("q")

        real_started = run_ledger.Slot.tool_started

        def die_before_b(slot, step, i):
            if i == 1:
                raise _Crash()
            return real_started(slot, step, i)

        def run(led):
            with patch.object(ollama_client, "_chat_once", fake_once), \
                    patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                    patch.object(ollama_client.tools, "run_tool", fake_tool), \
                    ollama_client.run_ledger(led):
                return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                          [{"function": {"name": "search_docs"}}])

        led = run_ledger.RunLedger.create("c4", "stream", {})
        with patch.object(run_ledger.Slot, "tool_started", die_before_b), self.assertRaises(_Crash):
            run(led)
        self.assertEqual((self.tool_runs, self.model_calls), (["a"], 1))
        point = run_ledger.RunLedger("c4").open_call().resume_point()
        self.assertEqual((point["step"], point["tools_done"], point["in_tool"]), (1, 1, False))
        self.assertEqual(run(run_ledger.RunLedger("c4")), ("final answer", None))
        self.assertEqual(self.tool_runs, ["a", "b"], "a not re-run; b, which never started, runs now")
        self.assertEqual(self.model_calls, 2, "turn 1 was not re-asked")

    def test_park_is_admin_only(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        self.assertEqual(c.post("/runs/park", json={"user": "NotAnAdmin", "on": True}).status_code, 403)
        self.assertFalse(ollama_client.parking())

    def test_without_a_ledger_nothing_is_written(self):
        self._run(None)
        self.assertFalse(run_ledger.ROOT.exists())


class SuspendTests(_TmpRoot):
    """#37 Quick Resume: suspend ONE run to storage on demand - it stops at a natural point, frees its thread and
    slot, waits on disk through restarts, and continues on POST /runs/resume."""
    USER = "SuspendUser"

    def test_a_suspended_run_waits_on_disk_and_resumes_on_request(self):
        cid = "cid-suspend-1"
        q = app.Q(text="suspend me", user=self.USER)
        q.cancel_id = cid
        app._accept_turn(q, cid)
        chat, led = q.chat_id, run_ledger.RunLedger(cid)
        calls = []

        def fake_once(host, model, messages, *a, **k):
            calls.append(1)
            return {"message": {"role": "assistant", "content": "finished after the resume"}}

        def core(q, status, diag):
            ans, _ = ollama_client.chat("h", "m", [{"role": "user", "content": q.text}], [])
            app._persist(q, q.text.strip(), ans)
            return {"route": "local", "answer": ans}

        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(app, "_run_query_core", core):
            ollama_client.suspend_run(cid)
            self.assertEqual(app._stream_worker(q, cid), None)
            self.assertEqual(calls, [], "it stopped at the first natural point, before any model turn")
            self.assertTrue(led.info().get("suspended"))
            self.assertFalse(live_runs.get(cid).done, "not over: the page keeps watching")
            self.assertEqual([p["cancel_id"] for p in history.pending(self.USER, chat)], [cid])
            view = app.runs_parked()
            self.assertEqual((view["running"], view["suspended"]), (0, [cid]),
                             "a deploy's park does not wait for a run that is already on disk")
            self.assertEqual(view["running_ids"], [], "a suspended run is not listed as in flight")

            app._resume_interrupted_runs()                      # a restart does not wake it
            self.assertEqual(calls, [])
            self.assertFalse(live_runs.get(cid).done)

            from fastapi.testclient import TestClient
            c = TestClient(app.app)
            admin = sorted(app.config.ADMIN_USERS)[0]
            self.assertEqual(c.post("/runs/resume", json={"user": "NotAnAdmin", "run_id": cid}).status_code, 403)
            self.assertEqual(c.post("/runs/resume", json={"user": admin, "run_id": cid}).json()["resumed"], True)
            for _ in range(250):
                f = live_runs.get(cid)
                if f is not None and f.done:
                    break
                time.sleep(0.02)
        self.assertEqual(live_runs.get(cid).events[-1]["result"]["answer"], "finished after the resume")
        self.assertEqual(calls, [1])
        self.assertEqual(history.pending(self.USER, chat), [])
        self.assertEqual([m["content"] for m in history.load(self.USER, chat)],
                         ["suspend me", "finished after the resume"])

    def test_suspend_is_admin_only_and_needs_a_running_run(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        run_ledger.RunLedger.create("cid-s2", "stream", {})
        admin = sorted(app.config.ADMIN_USERS)[0]
        self.assertEqual(c.post("/runs/suspend", json={"user": "NotAnAdmin", "run_id": "cid-s2"}).status_code, 403)
        self.assertEqual(c.post("/runs/suspend", json={"user": admin, "run_id": "nope"}).status_code, 404)
        self.assertTrue(c.post("/runs/suspend", json={"user": admin, "run_id": "cid-s2"}).json()["suspending"])
        self.addCleanup(ollama_client.suspend_run, "cid-s2", False)

    def test_a_suspended_absurd_run_keeps_its_ledger_and_frees_the_27b_lane(self):
        rid = "absurd-susp-1"
        app.ABSURD.create(rid, self.USER, "deep work", "1h", 3600, title="t")
        led = run_ledger.RunLedger.create("absurd-" + rid, "absurd", {"run_id": rid, "q": {}})

        def worker(run_id, q):
            app.ABSURD.update(run_id, status=app.absurd.RUNNING)
            raise ollama_client.RunSuspended(run_id)
        with patch.object(app, "_absurd_worker", worker):
            app._absurd_run(rid, app.Q(text="deep work", user=self.USER), False)
        self.assertEqual(app.ABSURD.get(rid)["status"], app.absurd.SUSPENDED)
        self.assertFalse(app.ABSURD.has_active(), "a suspended run does not hold the one-big-run lane")
        self.assertTrue(led.info().get("suspended"))

    def test_a_suspend_asked_for_a_run_that_finished_first_does_not_linger(self):
        q = app.Q(text="quick", user=self.USER)
        q.cancel_id = "cid-s5"
        app._accept_turn(q, "cid-s5")
        ollama_client.suspend_run("cid-s5")
        app._end_turn(q, "cid-s5", {"route": "local", "answer": "done"})
        self.assertNotIn("cid-s5", ollama_client._SUSPEND)

    def test_a_call_made_inside_a_tool_never_suspends(self):
        led = run_ledger.RunLedger.create("cid-s3", "stream", {})
        top = led.open_call()
        child = led.open_call(top)
        ollama_client.suspend_run("cid-s3")
        self.addCleanup(ollama_client.suspend_run, "cid-s3", False)
        ollama_client._suspend_here(child)                 # no raise: its parent stops at its own next point
        with self.assertRaises(ollama_client.RunSuspended):
            ollama_client._suspend_here(top)


class StreamLaneResumeTests(_TmpRoot):
    USER = "ResumeLaneUser"     # not a test* user: those are ephemeral and persist nothing

    def _interrupted_run(self, text="resume me"):
        cid = "cid-resume-1"
        q = app.Q(text=text, user=self.USER)
        q.cancel_id = cid
        chat = history.begin_turn(self.USER, None, text, cid)
        q.chat_id = chat
        led = run_ledger.RunLedger.create(cid, "stream", {"q": q.model_dump()})
        return cid, chat, led

    def _wait_done(self, cid):
        for _ in range(250):
            f = live_runs.get(cid)
            if f is not None and f.done:
                return f
            time.sleep(0.02)
        self.fail("the resumed run never finished")

    def test_the_restart_resumes_the_run_under_the_same_id(self):
        cid, chat, led = self._interrupted_run()
        seen = {}

        def core(q, status, diag):
            seen["cid"], seen["chat"] = q.cancel_id, q.chat_id
            seen["ledger_on_thread"] = getattr(ollama_client._CALL_CONTEXT, "run_ledger", None) is not None
            app._persist(q, q.text.strip(), "resumed answer")
            return {"route": "local", "answer": "resumed answer"}
        with patch.object(app, "_run_query_core", core):
            app._resume_interrupted_runs()
            feed = self._wait_done(cid)
        self.assertEqual((seen["cid"], seen["chat"]), (cid, chat))
        self.assertTrue(seen["ledger_on_thread"], "the resumed run checkpoints too")
        self.assertEqual(feed.events[-1]["result"]["answer"], "resumed answer")
        self.assertEqual(history.pending(self.USER, chat), [])
        self.assertEqual([m["content"] for m in history.load(self.USER, chat)], ["resume me", "resumed answer"])
        for _ in range(100):
            if not led.dir.exists():
                break
            time.sleep(0.02)
        self.assertFalse(led.dir.exists(), "the ledger goes once the run has ended")

    def test_a_run_that_saved_nothing_since_its_last_resume_is_stopped_not_looped(self):
        cid, chat, led = self._interrupted_run()
        led.mark_resumed(led.progress_mark())            # resumed once already, nothing saved since
        called = []
        with patch.object(app, "_run_query_core", lambda *a, **k: called.append(1)):
            app._resume_interrupted_runs()
        feed = live_runs.get(cid)
        self.assertTrue(feed.done)
        self.assertEqual(feed.events[-1]["result"]["route"], "error")
        self.assertIn("without saving any progress", feed.events[-1]["result"]["message"])
        self.assertEqual(called, [])
        self.assertEqual(history.pending(self.USER, chat), [])
        self.assertFalse(led.dir.exists())

    def _ended_in_an_error(self, done=False):
        """A run that checkpointed inside turn 3, before its tool 0, then ended with an error."""
        cid, chat, led = self._interrupted_run()
        slot = led.open_call()
        slot.save([{"role": "user", "content": "resume me"},
                   {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "coding_review"}}]}],
                  3, tools_done=0)
        if done:
            slot.done(["an answer", None])
        q = app.Q(text="resume me", user=self.USER)
        q.cancel_id, q.chat_id = cid, chat
        app._end_turn(q, cid, {"route": "error", "message": "boom"})
        self.assertFalse(led.dir.exists(), "ended: the ledger is a save now")
        return cid, chat

    def test_a_run_that_ended_in_an_error_continues_from_its_saved_checkpoint(self):
        cid, chat = self._ended_in_an_error()
        seen = {}

        def core(q, status, diag):
            led = ollama_client._CALL_CONTEXT.run_ledger
            seen["point"] = led.open_call().resume_point()
            seen["pending"] = [p["cancel_id"] for p in history.pending(self.USER, chat)]
            app._persist(q, q.text.strip(), "finished after the restore")
            return {"route": "local", "answer": "finished after the restore"}
        with patch.object(app, "_run_query_core", core):
            out = app.runs_resume(app.RunSaveReq(user=sorted(app.config.ADMIN_USERS)[0], run_id=cid))
            feed = self._wait_done(cid)
        self.assertTrue(out["resumed"] and out["from_save"].startswith(cid))
        self.assertEqual((seen["point"]["step"], seen["point"]["tools_done"], seen["point"]["in_tool"]), (3, 0, False),
                         "it picks up before the tool that crashed, and runs it")
        self.assertEqual(len(seen["point"]["messages"]), 2)
        self.assertEqual(seen["pending"], [cid], "the chat shows it working again")
        self.assertEqual(feed.events[-1]["result"]["answer"], "finished after the restore")
        self.assertEqual([m["content"] for m in history.load(self.USER, chat)][-2:], ["resume me", "finished after the restore"])
        self.assertEqual([s["run_id"] for s in run_ledger.saved()].count(cid) <= 1, True)

    def test_later_runs_do_not_rotate_out_a_run_that_can_still_be_continued(self):
        cid, _chat = self._ended_in_an_error()
        with patch.object(app.config, "RUN_SAVES_KEEP", 2):
            for i in range(4):
                led = run_ledger.RunLedger.create("later-%d" % i, "stream", {})
                led.open_call().done(["an answer", None])
                led.finish()
            kept = [s["run_id"] for s in run_ledger.saved()]
            self.assertIn(cid, kept, "the unfinished run is still there to continue")
            self.assertEqual(sorted(k for k in kept if k != cid), ["later-2", "later-3"], "finished runs still roll")
            with patch.object(run_ledger, "RESUMABLE_KEEP_S", -1):
                led = run_ledger.RunLedger.create("later-9", "stream", {})
                led.open_call().done(["an answer", None])
                led.finish()
            self.assertNotIn(cid, [s["run_id"] for s in run_ledger.saved()], "it does age out")

    def test_a_run_that_finished_its_answer_is_not_restored_and_only_an_admin_may_ask(self):
        cid, _chat = self._ended_in_an_error(done=True)
        with self.assertRaises(app.HTTPException) as e:
            app.runs_resume(app.RunSaveReq(user=sorted(app.config.ADMIN_USERS)[0], run_id=cid))
        self.assertEqual(e.exception.status_code, 404)
        self.assertIn("nothing to continue", e.exception.detail)
        self.assertEqual([s["run_id"] for s in run_ledger.saved()], [cid], "the save is left where it was")
        with self.assertRaises(app.HTTPException) as e:
            app.runs_resume(app.RunSaveReq(user=self.USER, run_id=cid))
        self.assertEqual(e.exception.status_code, 403)

    def test_accept_writes_the_ledger_and_the_end_removes_it(self):
        q = app.Q(text="hello", user=self.USER)
        app._accept_turn(q, "cid-accept")
        info = run_ledger.RunLedger("cid-accept").info()
        self.assertEqual(info["lane"], "stream")
        self.assertEqual(app.Q(**info["request"]["q"]).chat_id, q.chat_id, "the pinned chat is in the record")
        app._end_turn(q, "cid-accept", {"route": "local", "answer": "x"})
        self.assertFalse(run_ledger.RunLedger("cid-accept").dir.exists())

    def test_an_ephemeral_run_writes_no_ledger(self):
        app._accept_turn(app.Q(text="hi", user="test"), "cid-eph")
        self.assertFalse(run_ledger.RunLedger("cid-eph").dir.exists())


class AResumedRunGoesBackToItsOwnRow(_TmpRoot):
    """A resumed run must not be re-triaged to a different model or box; it waits for the original resources named in its checkpoint."""

    _run = ChatReplayTests._run          # the real chat() loop with the model and the tools faked

    def test_the_checkpoint_names_the_box_and_model_until_the_call_is_done(self):
        led = run_ledger.RunLedger.create("h1", "stream", {})
        self.assertIsNone(led.served(), "a new run has no row yet")
        with self.assertRaises(_Crash):
            self._run(led, crash_on_tool="n1")
        self.assertEqual(run_ledger.RunLedger("h1").served(), {"host": "h", "model": "m", "num_ctx": None})
        self._run(run_ledger.RunLedger("h1"))
        self.assertIsNone(run_ledger.RunLedger("h1").served(), "a finished call is not a row to go back to")

    def test_a_checkpoint_saved_before_this_was_recorded_names_nothing(self):
        led = run_ledger.RunLedger.create("h2", "stream", {})
        led.open_call().save([{"role": "user", "content": "go"}], 1)
        self.assertIsNone(run_ledger.RunLedger("h2").served())

    def test_the_row_is_the_unfinished_top_call_not_an_earlier_finished_one(self):
        led = run_ledger.RunLedger.create("h3", "stream", {})
        first, second = led.open_call(), led.open_call()
        first.served = {"host": "http://a", "model": "small", "num_ctx": 1}
        first.save([{"role": "user", "content": "x"}], 1)
        first.done(("ans", None))
        second.served = {"host": "http://b", "model": "lead", "num_ctx": 2}
        second.save([{"role": "user", "content": "y"}], 1)
        self.assertEqual(run_ledger.RunLedger("h3").served()["model"], "lead")

    def test_the_home_is_the_runs_own_box_and_only_inside_the_run(self):
        import config
        import scheduler
        self.assertIsNone(app._resumed_home(), "no run on this thread")
        led = run_ledger.RunLedger.create("h4", "stream", {})
        slot = led.open_call()
        slot.served = {"host": config.HEAVY_HOST, "model": "qwen3.8-flash-next-strata-q2_0", "num_ctx": 125952}
        slot.save([{"role": "user", "content": "go"}], 3)
        with ollama_client.run_ledger(run_ledger.RunLedger("h4")):
            home = app._resumed_home()
        self.assertEqual(tuple(home), (scheduler.LEAD_BOX, "qwen3.8-flash-next-strata-q2_0", 125952))
        with patch.object(ollama_client, "lead_model", lambda host, model, job: "qwen3.8-flash-next-strata-q2_0"):
            self.assertTrue(app._is_lead_window(home), "the lead's row takes the lead's path")
        slot.served = {"host": "http://10.9.9.9:11434", "model": "m", "num_ctx": 1}
        slot.save([{"role": "user", "content": "go"}], 4)
        with ollama_client.run_ledger(run_ledger.RunLedger("h4")):
            self.assertIsNone(app._resumed_home(), "a box that is not on the menu is not guessed at")

    def test_a_busy_box_is_waited_for_and_said_once(self):
        import config
        import scheduler
        q = app.Q(text="go", user="test")
        held = iter([{"holder": "labengine build"}, {"holder": "labengine build"}, None])
        said, slept = [], []
        with patch.object(scheduler, "occupied_for", lambda *a, **k: next(held)):
            waited = app._wait_while_held(q, scheduler.LEAD_BOX, config.HEAVY_HOST, "lead", said.append, slept.append,
                                          whose="this run", tag="RESUME")
        self.assertTrue(waited)
        self.assertEqual(len(slept), 2)
        self.assertEqual(len([s for s in said if "this run waits for it" in s and "labengine build" in s]), 1)
        with patch.object(scheduler, "occupied_for", lambda *a, **k: None):
            self.assertFalse(app._wait_while_held(q, scheduler.LEAD_BOX, config.HEAVY_HOST, "lead", said.append,
                                                  slept.append))
        self.assertEqual(len(said), 1, "a free box is taken without a word")

    def test_the_serve_path_goes_home_and_waits_before_any_triage(self):
        """Source-level pin for a seam with no cheap behavioural harness (the whole turn; test_window_pickup's precedent)."""
        import inspect
        src = inspect.getsource(app._run_query_core_body)
        self.assertLess(src.index("_resumed_home()"), src.index("_mccoy_serve("))
        self.assertLess(src.index("_wait_while_held("), src.index('"resumed on its own row"'))


class AParkedTurnIsAskedAgain(_TmpRoot):
    """Test that a parked turn is resumed from the pre-turn checkpoint, re-asking the same turn."""

    class _Parked(ollama_client.httpx.ReadError):
        parked = True

    def run_chat(self, led, parks, suspend=None):
        self.asked = getattr(self, "asked", 0)
        left = [parks]

        def once(host, model, messages, *a, **k):
            self.asked += 1
            self.flag_seen = bool(getattr(ollama_client._CALL_CONTEXT, "reask_parked", False))
            if left[0]:
                left[0] -= 1
                if suspend:
                    ollama_client.suspend_run(suspend)     # the suspend arrives WHILE the turn is being written
                raise self._Parked("window w parked this turn mid-answer (parked by its caller); its state is saved")
            return {"message": {"role": "assistant", "content": "the whole answer"}}

        # CHAT_RETRIES 0: one spent retry would end the call with a ChatError, so an answer proves none was spent
        with patch.object(ollama_client, "_chat_once", once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.config, "CHAT_RETRIES", 0), \
                ollama_client.run_ledger(led):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}], [])

    def test_it_is_asked_again_at_once_and_no_retry_is_spent(self):
        led = run_ledger.RunLedger.create("p1", "stream", {})
        self.assertEqual(self.run_chat(led, parks=3), ("the whole answer", None))
        self.assertEqual(self.asked, 4, "three parks, then the answer - with zero retries allowed")
        self.assertTrue(self.flag_seen, "the turn is marked as one its caller asks again")
        self.assertFalse(getattr(ollama_client._CALL_CONTEXT, "reask_parked", False), "and the mark does not outlive it")

    def test_a_run_asked_to_suspend_stops_there_and_its_resume_asks_that_turn(self):
        led = run_ledger.RunLedger.create("p2", "stream", {})
        self.addCleanup(ollama_client.suspend_run, "p2", False)
        with self.assertRaises(ollama_client.RunSuspended):
            self.run_chat(led, parks=1, suspend="p2")
        point = run_ledger.RunLedger("p2").open_call().resume_point()
        self.assertEqual((point["step"], point["tools_done"]), (1, None), "held on the checkpoint before the parked turn")
        self.assertEqual(self.run_chat(run_ledger.RunLedger("p2"), parks=0), ("the whole answer", None))
        self.assertEqual(self.asked, 2, "the parked ask, then the same turn once more after the resume")


class ALoopedTurnIsToldAndAskedAgain(_TmpRoot):
    """A turn the factory's loop rule ended can re-enter as a transient read error.
    The model is asked again verbatim without a transport retry; consecutive
    CODING_IDLE_TURNS answers end the call."""

    class _Looped(ollama_client.httpx.ReadError):
        looped = True

    def run_chat(self, loops):
        self.seen = []
        left = [loops]

        def once(host, model, messages, *a, **k):
            self.seen.append([dict(m) for m in messages])
            if left[0]:
                left[0] -= 1
                raise self._Looped("window w ended this turn: the model's output was repeating itself")
            return {"message": {"role": "assistant", "content": "the answer"}}

        # CHAT_RETRIES 0: one spent retry would end the call, so an answer proves a loop spends none
        with patch.object(ollama_client, "_chat_once", once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.config, "CHAT_RETRIES", 0), \
                patch.object(ollama_client.config, "CODING_IDLE_TURNS", 3):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}], [])

    def test_the_model_is_told_once_and_no_retry_is_spent(self):
        self.assertEqual(self.run_chat(loops=2), ("the answer", None))
        self.assertEqual(len(self.seen), 3, "two looped answers, then the answer")
        self.assertEqual([m["role"] for m in self.seen[0]], ["user"], "the first ask is the turn as it was")
        notes_in = [[m for m in ask if "YOUR LAST ANSWER WAS CUT OFF" in str(m.get("content"))] for ask in self.seen]
        self.assertEqual([len(n) for n in notes_in], [0, 1, 1], "told once: the second loop adds no second note")
        self.assertIn("after 3 such answers", notes_in[1][0]["content"])

    def test_three_in_a_row_end_the_call_and_say_it_was_a_loop(self):
        with self.assertRaises(ollama_client.ChatError) as cm:
            self.run_chat(loops=3)
        self.assertEqual(len(self.seen), 3, "the third looped answer ends it: no fourth ask")
        self.assertIn("loop 3 times in a row", str(cm.exception))
        self.assertIn("not a network fault", str(cm.exception))

    def test_a_job_that_loops_three_times_stops_with_a_handoff_not_a_failure(self):
        """A job that loops stops with a handoff; a chat that loops raises."""
        whys = []
        with ollama_client.call_context(job=True, budget_label="8 hours"), \
                patch.object(ollama_client, "_explain", side_effect=lambda *a, **k: whys.append(k.get("why")) or "handoff"):
            answer, esc = self.run_chat(loops=3)
        self.assertEqual(len(self.seen), 3, "no fourth ask of the looping model")
        self.assertIn("loop 3 times in a row", whys[0])
        self.assertTrue(answer.startswith("handoff") and "not a finished answer" in answer, answer)


class ASuspendGivesTheCardAway(unittest.TestCase):
    """Suspend releases the card immediately without touching other claims."""

    def release(self, claim, model="m", who="U"):
        import scheduler
        freed = []
        url = scheduler.CARDS[scheduler.BRAIN]["url"] if hasattr(scheduler, "BRAIN") else \
            scheduler.CARDS["bighonker"]["url"]
        with patch.object(scheduler, "active_claim", lambda name, res: claim), \
                patch.object(scheduler, "release", lambda name, res: freed.append((name, res))):
            ok = ollama_client.release_live(url, model, who, "its run was suspended")
        return ok, freed

    def test_the_runs_own_claim_for_its_model_is_released(self):
        import scheduler
        ok, freed = self.release({"holder": "hai-chat:U", "note": "m"})
        self.assertTrue(ok)
        self.assertEqual(freed, [("bighonker", scheduler.LIVE)])

    def test_another_users_claim_or_another_model_is_left_alone(self):
        self.assertEqual(self.release({"holder": "hai-chat:Someone", "note": "m"}), (False, []))
        self.assertEqual(self.release({"holder": "hai-chat:U", "note": "another-model"}), (False, []))
        self.assertEqual(self.release(None), (False, []))


class AbsurdLaneResumeTests(_TmpRoot):
    def test_an_interrupted_absurd_run_goes_back_to_running(self):
        import absurd
        reg = absurd.AbsurdRegistry(os.path.join(self.tmp, "absurd"))
        run_id = absurd.new_run_id()
        reg.create(run_id, "AbsUser", "think long", "1h", 3600, title="t")
        reg.update(run_id, status=absurd.ERROR, error="interrupted by an orchestrator restart")
        q = app.Q(text="think long", user="AbsUser", effort_mode="think_hard", ephemeral=True)
        led = run_ledger.RunLedger.create("absurd-" + run_id, "absurd",
                                          {"run_id": run_id, "q": q.model_dump(), "decomposed": False})
        ran = threading.Event()

        def worker(rid, q2):
            ran.set()
            self.assertIsNotNone(getattr(ollama_client._CALL_CONTEXT, "run_ledger", None))
            reg.update(rid, status=absurd.DONE)
        with patch.object(app, "ABSURD", reg), patch.object(app, "_absurd_worker", worker):
            app._resume_interrupted_runs()
            self.assertTrue(ran.wait(5), "the Absurd worker was restarted")
            for _ in range(100):
                if not led.dir.exists():
                    break
                time.sleep(0.02)
        self.assertEqual(reg.get(run_id)["status"], absurd.DONE)
        self.assertFalse(led.dir.exists())


if __name__ == "__main__":
    unittest.main()
