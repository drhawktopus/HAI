"""Tests run against a single mock meter instance consumed by the model, finish gate, and deck. Uses static fixtures, not live systems."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import hai_jobs
import ollama_client
import run_habits
import run_ledger
from run_habits import Habits
from test_run_resume import _Crash, _TmpRoot

SEEN = json.dumps({"path": "x", "seen_by": "you"})
BLIND = json.dumps({"seen_by": None, "error": "The model serving this job is not set up to take pictures, so nothing looked"})


def render(n=3, sheet_seen=False, stem="job/renders/p"):
    labels = ["load", "drag", "key", "click", "scroll"]
    d = {"kind": "page", "rendered": True, "path": "job/p.html",
         "shots": [{"after": labels[i % 5], "path": "%s-%02d-%s.png" % (stem, i, labels[i % 5])} for i in range(n)]}
    if sheet_seen:
        d["sheet"] = {"path": stem + "-sheet-abcd1234.png", "frames": n, "seen_by": "you"}
    return json.dumps(d)


class TheMeterCounts(unittest.TestCase):
    def test_a_second_whole_file_write_is_said_with_the_count_and_a_patch_is_not(self):
        h = Habits()
        self.assertEqual(h.note("coding_create", {"path": "job/a.html"}, "ok"), "")
        self.assertEqual(h.note("coding_patch", {"path": "job/a.html"}, "ok"), "")
        said = h.note("coding_create", {"path": "job/a.html"}, "ok")
        self.assertIn("whole-file write 2 of a.html", said)
        self.assertIn("edits in place: 1", said)
        self.assertIn("coding_patch", said)
        self.assertIn("whole-file write 3", h.note("coding_create", {"path": "job/a.html"}, "ok"))
        self.assertEqual(h.note("coding_create", {"path": "job/b.html"}, "ok"), "", "another file's first write says nothing")

    def test_a_tool_that_failed_changes_no_count(self):
        h = Habits()
        h.note("coding_create", {"path": "a"}, "ok")
        self.assertEqual(h.note("coding_create", {"path": "a"}, "refused", snag=True), "")
        self.assertEqual(h.s["files"]["a"]["whole"], 1)
        self.assertEqual(h.note("coding_render", {}, "not json", snag=False), "")
        self.assertEqual(h.s["renders"], 0)

    def test_a_render_nobody_looked_at_is_said_and_a_seen_sheet_covers_every_frame(self):
        h = Habits()
        said = h.note("coding_render", {"path": "job/p.html"}, render(4))
        self.assertIn("4 pictures and none has been looked at", said)
        self.assertIn("after an interaction", said)
        self.assertEqual(h.note("coding_render", {"path": "job/p.html"}, render(4, sheet_seen=True)), "")
        self.assertEqual(sorted(h.s["seen"]), sorted(h.s["last"]))
        self.assertEqual((h.s["renders"], h.s["frames"], h.s["looks"]["sheet"]), (2, 8, 1))

    def test_looking_only_at_the_load_frame_is_said_and_a_look_after_an_interaction_is_counted(self):
        h = Habits()
        h.note("coding_render", {}, render(3))
        said = h.note("coding_look", {"path": "job/renders/p-00-load.png"}, SEEN)
        self.assertIn("2 pictures of that render, taken after an interaction, have not been looked at", said)
        self.assertEqual(h.note("coding_look", {"path": "job/renders/p-01-drag.png"}, SEEN), "")
        self.assertEqual((h.s["looks"]["load"], h.s["looks"]["after"]), (1, 1))
        self.assertIn("1 picture of that render", h.note("coding_look", {"path": "job/renders/p-00-load.png"}, SEEN))

    def test_a_reference_never_looked_at_is_said_once_and_a_reference_look_is_counted(self):
        h = Habits()
        h.note("fetch_url", {"url": "https://x"}, "HTTP 200\n## Pictures on this page (95)\n...")
        said = h.note("coding_render", {}, render(2, sheet_seen=True))
        self.assertIn("list 95 pictures and have looked at none", said)
        self.assertEqual(h.note("coding_render", {}, render(2, sheet_seen=True)), "", "said once, not at every render")
        h2 = Habits()
        h2.note("fetch_url", {}, "## Pictures on this page (3)")
        h2.note("coding_look", {"path": "job/logo.png"}, SEEN)
        self.assertEqual(h2.s["looks"]["reference"], 1)
        self.assertEqual(h2.note("coding_render", {}, render(2, sheet_seen=True)), "")

    def test_a_model_that_takes_no_pictures_is_never_told_to_look(self):
        h = Habits()
        h.note("coding_render", {}, render(3))
        h.note("coding_look", {"path": "job/renders/p-00-load.png"}, BLIND, snag=True)
        self.assertIs(h.s["sees"], False)
        self.assertEqual(h.note("coding_render", {}, render(3)), "")
        self.assertEqual(h.finish_reasons(), [])

    def test_it_never_raises_on_arguments_it_does_not_expect(self):
        h = Habits()
        for bad in (None, "text", 5, ["a"]):
            self.assertEqual(h.note("coding_render", bad, bad), "")
            self.assertEqual(h.note(None, bad, None), "")


class TheGateAsksTheSameMeter(unittest.TestCase):
    def test_a_run_that_never_rendered_is_never_held(self):
        h = Habits()
        h.note("coding_patch", {"path": "a.py"}, "ok")
        self.assertEqual(h.finish_reasons(), [])

    def test_a_change_after_the_last_render_holds_the_finish_until_it_is_rendered_again(self):
        h = Habits()
        h.note("coding_render", {}, render(2, sheet_seen=True))
        self.assertEqual(h.finish_reasons(), [])
        h.note("coding_patch", {"path": "job/p.html"}, "ok")
        self.assertIn("changed after the last render", h.finish_reasons()[0])
        h.note("coding_render", {}, render(2, sheet_seen=True))
        self.assertEqual(h.finish_reasons(), [])

    def test_a_seeing_model_that_looked_at_nothing_of_its_last_render_is_held_and_one_look_lets_it_go(self):
        h = Habits()
        h.note("coding_look", {"path": "job/ref.png"}, SEEN)                      # so the meter knows it can see
        h.note("coding_render", {}, render(3))
        self.assertIn("3 pictures and none was looked at", h.finish_reasons()[0])
        h.note("coding_look", {"path": "job/renders/p-02-key.png"}, SEEN)
        self.assertEqual(h.finish_reasons(), [], "its own look, at any frame, is a look: nothing is asked beyond it")


class TheStateSurvivesARestartAndTheDeckReadsIt(unittest.TestCase):
    def test_the_state_round_trips_through_json_and_goes_on_counting(self):
        h = Habits()
        h.note("coding_create", {"path": "job/a.html"}, "ok")
        h.note("coding_render", {}, render(3))
        h.note("coding_look", {"path": "job/renders/p-01-drag.png"}, SEEN)
        back = Habits(json.loads(json.dumps(h.state())))
        self.assertEqual(back.state(), h.state())
        self.assertIn("whole-file write 2", back.note("coding_create", {"path": "job/a.html"}, "ok"))
        self.assertIn("changed after the last render", back.finish_reasons()[0])

    def test_the_line_names_renders_looks_and_the_most_edited_file(self):
        h = Habits()
        self.assertEqual(h.line(), "")
        for _ in range(3):
            h.note("coding_create", {"path": "job/buttocks.html"}, "ok")
        h.note("coding_patch", {"path": "job/buttocks.html"}, "ok")
        h.note("coding_render", {}, render(3))
        h.note("coding_look", {"path": "job/renders/p-01-drag.png"}, SEEN)
        self.assertEqual(h.line(), "renders 1, looks 1 (after an interaction 1, sheets 0, reference 0); "
                                   "buttocks.html: 3 whole-file writes, 1 edits in place")

    def test_the_short_form_counts_a_sheet_as_a_look_after_an_interaction_and_the_first_write_is_not_a_rewrite(self):
        h = Habits()
        self.assertEqual(h.short(), "", "a run that has rendered and written nothing adds nothing to the status")
        h.note("coding_create", {"path": "job/page.html"}, "ok")
        self.assertEqual(h.short(), "page.html: 0 rewrites, 0 edits")
        h.note("coding_create", {"path": "job/page.html"}, "ok")
        h.note("coding_patch", {"path": "job/page.html"}, "ok")
        h.note("coding_render", {}, render(6, sheet_seen=True))
        h.note("coding_look", {"path": "job/renders/p-00-load.png"}, SEEN)
        self.assertEqual(h.short(), "1 renders, 2 looks (1 after an interaction); page.html: 1 rewrites, 1 edits")

    def test_a_running_job_carries_its_line_and_the_desks_record_is_not_touched(self):
        rows = [{"id": "j1", "status": "running", "run_id": "r1", "spec": {"kind": hai_jobs.KIND, "user": "Alex"}},
                {"id": "j2", "status": "done", "run_id": "r2", "spec": {"kind": hai_jobs.KIND, "user": "Alex"}}]
        q = mock.Mock()
        q.all_jobs.return_value = rows
        with mock.patch.object(hai_jobs, "_q", return_value=q), \
                mock.patch.dict(run_habits.LIVE, {"r1": "renders 2, looks 1", "r2": "old"}, clear=True):
            got = hai_jobs.jobs()
        self.assertEqual(got[0]["habits"], "renders 2, looks 1")
        self.assertNotIn("habits", got[1], "a finished job shows no live line")
        self.assertNotIn("habits", rows[0], "the record handed in is not changed")


CODING_TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}}
                for n in ("coding_patch", "coding_create", "coding_test", "coding_render", "coding_look")]


def _reply(content="", calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return {"message": msg, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 20}


def _passed():
    return json.dumps({"profile": "unit", "returncode": 0, "stdout": "Ran 3 tests", "stderr": ""})


class TheLoopSaysItAndTheGateHoldsIt(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        p = mock.patch.object(config, "STATE_DIR", self.state)
        p.start()
        self.addCleanup(p.stop)

    def switch(self, **on):
        with open(os.path.join(self.state, "hai-habits.json"), "w", encoding="utf-8") as f:
            json.dump(on, f)

    def run_loop(self, script, tool_results, job=True):
        seq, seen = list(script), []
        results = {k: list(v) for k, v in tool_results.items()}

        def fake_once(host, model, messages, *a, **k):
            seen.append([dict(m) for m in messages])
            return seq.pop(0)

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(ollama_client.tools, "run_tool", side_effect=lambda name, args, *a, **k: results[name].pop(0)), \
                mock.patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                mock.patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.call_context(job=job):
            answer, _esc = ollama_client.chat("http://h", "m", [{"role": "user", "content": "build the page"}],
                                              list(CODING_TOOLS), num_ctx=4096)
        self.assertFalse(seq, "the fake model had unused replies - the loop ended earlier than the script")
        return answer, seen

    SCRIPT = [_reply(calls=[("coding_create", {"path": "job/p.html"}), ("coding_create", {"path": "tests/test_p.py"}),
                            ("coding_test", {"profile": "unit"}), ("coding_render", {"path": "job/p.html"})]),
              _reply(calls=[("coding_create", {"path": "job/p.html"}), ("coding_test", {"profile": "unit"})]),
              _reply("done"),                                              # held: changed after the last render
              _reply(calls=[("coding_render", {"path": "job/p.html"})]),
              _reply("done, and I looked")]
    RESULTS = {"coding_create": ["created", "created", "created"], "coding_test": [_passed(), _passed()],
               "coding_render": [render(3, sheet_seen=True), render(3, sheet_seen=True)]}

    def test_switched_on_the_rewrite_is_said_in_its_result_and_the_finish_waits_for_a_last_render(self):
        self.switch(meter=True, finish_look=True)
        answer, seen = self.run_loop(list(self.SCRIPT), self.RESULTS)
        self.assertEqual(answer, "done, and I looked")
        tool_texts = [m["content"] for m in seen[-1] if m.get("role") == "tool"]
        self.assertTrue(any("whole-file write 2 of p.html" in t for t in tool_texts), tool_texts)
        refusals = [m["content"] for m in seen[-1] if m.get("role") == "user" and "FINISH REFUSED" in m.get("content", "")]
        self.assertEqual(len(refusals), 1)
        self.assertIn("changed after the last render", refusals[0])
        self.assertIn("coding_look", refusals[0], "the refusal names the model's own look as a way through")

    def test_switched_off_the_same_run_hears_nothing_and_is_not_held(self):
        script = self.SCRIPT[:3]
        answer, seen = self.run_loop(list(script), {"coding_create": ["created", "created", "created"],
                                                    "coding_test": [_passed(), _passed()],
                                                    "coding_render": [render(3, sheet_seen=True)]})
        self.assertEqual(answer, "done")
        self.assertFalse(any("How this run is working" in str(m.get("content")) for m in seen[-1]))
        self.assertFalse(any("FINISH REFUSED" in str(m.get("content")) for m in seen[-1]))

    def test_the_live_status_says_how_the_run_is_working_once_there_is_something_to_say(self):
        said = []
        script = [_reply(calls=[("coding_create", {"path": "job/p.html"})]),
                  _reply(calls=[("coding_render", {"path": "job/p.html"})]),
                  _reply(calls=[("coding_patch", {"path": "job/p.html"})]),
                  _reply("here you go")]
        seq = list(script)
        results = {"coding_create": ["created"], "coding_render": [render(6, sheet_seen=True)], "coding_patch": ["patched"]}
        with mock.patch.object(ollama_client, "_chat_once", side_effect=lambda *a, **k: seq.pop(0)), \
                mock.patch.object(ollama_client.tools, "run_tool", side_effect=lambda name, args, *a, **k: results[name].pop(0)), \
                mock.patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                mock.patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.call_context(job=False):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "build the page"}], list(CODING_TOOLS),
                               num_ctx=4096, status_callback=said.append)
        steps = [s for s in said if str(s).startswith("Tool step")]
        self.assertEqual(len(steps), 3, said)
        self.assertTrue(steps[0].endswith(": coding_create"), "nothing rendered or written yet: the status is as it was")
        self.assertTrue(steps[1].endswith(": coding_render · p.html: 0 rewrites, 0 edits"), steps[1])
        self.assertTrue(steps[2].endswith(": coding_patch · 1 renders, 1 looks (1 after an interaction); p.html: 0 rewrites, 0 edits"), steps[2])

    def test_a_plain_chat_turn_is_never_held_by_the_last_look(self):
        self.switch(meter=True, finish_look=True)
        script = [_reply(calls=[("coding_render", {"path": "job/p.html"}), ("coding_patch", {"path": "job/p.html"})]),
                  _reply("here you go")]
        answer, seen = self.run_loop(script, {"coding_render": [render(2)], "coding_patch": ["patched"]}, job=False)
        self.assertEqual(answer, "here you go")


class TheMeterSurvivesARestart(_TmpRoot, unittest.TestCase):   # TestCase named here: the hygiene check reads this file alone
    """Lossless is the standard: a job that rendered, changed its page and was cut by a restart still owes its last look
    when it comes back, exactly as it still owes its tests (test_run_resume.TheFinishGateSurvivesARestart)."""

    def _run(self, led, script):
        seq = list(script)

        def fake_once(host, model, messages, *a, **k):
            self.calls.append(list(messages))
            nxt = seq.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt

        def tool(fn, args, *a, **k):
            return {"coding_render": render(3, sheet_seen=True), "coding_test": _passed()}.get(fn, "ok")

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(ollama_client.tools, "run_tool", side_effect=tool), \
                mock.patch.object(ollama_client.tools, "habit_on", side_effect=lambda name: True), \
                mock.patch.object(ollama_client.tool_selection, "select", side_effect=lambda tools, *_a, **_k: (tools, None)), \
                mock.patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.run_ledger(led), ollama_client.call_context(job=True):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "build the page"}], list(CODING_TOOLS), num_ctx=100000)

    def test_a_resumed_job_still_owes_the_look_it_owed_before_the_restart(self):
        self.calls = []
        led = run_ledger.RunLedger.create("habits1", "stream", {})
        first = _reply(calls=[("coding_render", {"path": "job/p.html"}), ("coding_patch", {"path": "job/p.html"})])
        with self.assertRaises(_Crash):
            self._run(led, [first, _Crash()])
        resumed_from = len(self.calls)
        answer, _esc = self._run(run_ledger.RunLedger("habits1"),
                                 [_reply(calls=[("coding_test", {"profile": "unit"})]), _reply("done"),
                                  _reply(calls=[("coding_render", {"path": "job/p.html"})]), _reply("done, and I looked")])
        self.assertEqual(answer, "done, and I looked")
        after = self.calls[resumed_from:]
        said = [m.get("content") for m in after[-1] if m.get("role") == "user" and "FINISH REFUSED" in str(m.get("content"))]
        self.assertEqual(len(said), 1, "the run back after the restart knew the page changed after its last render")
        self.assertIn("changed after the last render", said[0])


if __name__ == "__main__":
    unittest.main()
