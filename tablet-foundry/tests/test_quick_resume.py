"""Verify quick resume restores state and continues from the exact token; any mismatch must fail-safe to re-ask."""
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tablet_foundry as tf
import tolkien_llm as tv

_STATE = mock.patch.object(tf, "STATE_DIR", None)
GOOD = "```python\ndef add(a, b):\n    return a + b\n```"
SPEC = {"kind": "llm", "name": "qr1", "model": "m.gguf", "payload": "/p.py", "tasks": ["add"], "replicates": 1}
CH = {"a": 11, "b": 12, "c": 13, "d": 14}
INV = {v: k for k, v in CH.items()}


def setUpModule():
    _STATE.new = tempfile.mkdtemp(prefix="qr_state_")
    _STATE.start()


def tearDownModule():
    _STATE.stop()
    shutil.rmtree(_STATE.new, ignore_errors=True)


def fake_payload():
    m = types.ModuleType("fake_payload")
    m.SUFFIX = "\nReturn only code."
    m.TEMPERATURE = 0.6
    m.CONTEXT = 2048
    m.TASKS = {"add": {"prompt": "write add", "checks": ["lambda: add(2, 3) == 5"]}}
    m.CONTROL_TASKS = {"control_add": {"prompt": "control add", "checks": ["lambda: add(1, 1) == 2"]}}
    m.extract_code = lambda text: text.split("```python\n")[1].split("```")[0] if "```python" in text else ""

    def score(code, checks, work):
        ns = {}
        exec(code, ns)
        return sum(1 for c in checks if eval(c, ns)()), len(checks), ""
    m.score = score
    return m


class FakeResp:
    """An SSE stream as urlopen returns it; `after_first` runs once the first line is read (e.g. a stop slip)."""

    def __init__(self, objs, after_first=None):
        self.lines = [("data: " + json.dumps(o) + "\n").encode() for o in objs] + [b"data: [DONE]\n"]
        self.after_first = after_first

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for i, line in enumerate(self.lines):
            yield line
            if i == 0 and self.after_first:
                self.after_first()


class Base(unittest.TestCase):
    def setUp(self):
        os.makedirs(tv.slots_dir(), exist_ok=True)
        for f in os.listdir(tv.slots_dir()):
            os.remove(os.path.join(tv.slots_dir(), f))
        self.addCleanup(tv.RESUME.update, name=None, launch=None, cut=None, restored=None, log=None, dumped=False)
        self.logs = []

    def arm(self, name="qr1", launch=None):
        tv.RESUME.update(name=name, launch=launch or {"launch": 1}, cut=None, restored=None, log=self.logs.append,
                         dumped=False)

    def write_dump(self, name="qr1", launch=None, cut=True):
        b, j = tv._resume_files(name)
        with open(b, "wb") as f:
            f.write(b"slot")
        rec = {"launch": launch or {"launch": 1}, "pending_token": 13, "saved_tokens": 5, "at": "x",
               "cut": {"key": "k", "continuable": bool(cut), "reasoning": "r", "content": "c", "pre_wall_s": 4.0}}
        with open(j, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return b, j


class RequestKey(unittest.TestCase):
    def test_the_key_is_what_decides_the_answer_never_the_stream_knobs(self):
        a = {"messages": [{"role": "user", "content": "q"}], "temperature": 0, "seed": 1, "stream": True,
             "max_tokens": -1}
        b = dict(a, stream=False, max_tokens=10, timings_per_token=True)
        self.assertEqual(tv.request_key(a), tv.request_key(b))
        self.assertNotEqual(tv.request_key(a), tv.request_key(dict(a, messages=[{"role": "user", "content": "Q"}])))
        self.assertNotEqual(tv.request_key(a), tv.request_key(dict(a, tools=[{"type": "function"}])))


class DumpOnStop(Base):
    def test_an_unarmed_order_never_dumps(self):
        with mock.patch.object(tv, "_http_json") as h:
            self.assertIsNone(tv.dump_on_stop({"messages": []}, "", "", False))
        h.assert_not_called()

    def test_a_cut_answer_dumps_the_slot_its_pending_token_and_the_call(self):
        self.arm()
        body = {"messages": [{"role": "user", "content": "q"}], "seed": 1}
        sv = {"n_saved": 4139, "n_written": 9, "timings": {"save_ms": 3.0}, "pending_token": 424}
        with mock.patch.object(tv, "_http_json", return_value=sv) as h:
            rec = tv.dump_on_stop(body, "think", "ans", False, pre_wall=12.5, pre_tokens=129, log=self.logs.append)
        self.assertEqual(h.call_args[0][1:], ("/slots/0?action=save", {"filename": "qr1.resume.bin"}))
        with open(tv._resume_files("qr1")[1], encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual((on_disk["pending_token"], on_disk["cut"]["key"], on_disk["cut"]["pre_wall_s"]),
                         (424, tv.request_key(body), 12.5))
        self.assertEqual(rec["saved_tokens"], 4139)
        self.assertTrue(tv.RESUME["dumped"])

    def test_a_cut_inside_a_tool_call_keeps_the_prefix_but_never_continues_the_call(self):
        self.arm()
        with mock.patch.object(tv, "_http_json", return_value={"n_saved": 9, "pending_token": 5}):
            rec = tv.dump_on_stop({"messages": []}, "", "", True)
        self.assertFalse(rec["cut"]["continuable"], "the engine refuses to continue a message holding a tool call")
        self.assertEqual(rec["cut"]["key"], tv.request_key({"messages": []}), "still keyed: its re-ask restores it")

    def test_a_failed_save_is_todays_behaviour_never_an_error(self):
        self.arm()
        with mock.patch.object(tv, "_http_json", side_effect=RuntimeError("HTTP 501")):
            self.assertIsNone(tv.dump_on_stop({"messages": []}, "", "", False, log=self.logs.append))
        self.assertFalse(os.path.exists(tv._resume_files("qr1")[1]))
        self.assertTrue(any("no dump" in m for m in self.logs), self.logs)


class PrepareAndRestore(Base):
    """The dump is accepted at start and restored right before the cut call. A resumed order runs its control first, and any other request overwrites slot 0."""

    def test_nothing_dumped_prepares_nothing(self):
        self.arm()
        with mock.patch.object(tv, "_http_json") as h:
            self.assertIsNone(tv.prepare_resume())
        h.assert_not_called()

    def test_a_dump_for_another_launch_or_engine_is_retired_never_restored(self):
        self.arm(launch={"engine": "new"})
        b, j = self.write_dump(launch={"engine": "old"})
        with mock.patch.object(tv, "_http_json") as h:
            self.assertIsNone(tv.prepare_resume(log=self.logs.append))
        h.assert_not_called()
        self.assertFalse(os.path.exists(b) or os.path.exists(j))

    def test_start_restores_nothing_the_cut_call_restores_and_is_taken_once(self):
        self.arm()
        self.write_dump()
        with mock.patch.object(tv, "_http_json") as h:
            rec = tv.prepare_resume(log=self.logs.append)
        h.assert_not_called()                                # nothing touches slot 0 at start
        self.assertEqual((rec["saved_tokens"], tv.RESUME["cut"]["pending_token"]), (5, 13))
        tv.RESUME["cut"]["key"] = tv.request_key({"messages": [{"role": "user", "content": "q"}]})
        self.assertIsNone(tv._take_cut({"messages": [{"role": "user", "content": "other"}]}), "the control, say")
        restored = []
        with mock.patch.object(tv, "restore_for_cut", side_effect=lambda: restored.append(1) or True), \
             mock.patch.object(tv, "continue_for", return_value=("body", "", "", {"continued_from": 3,
                                                                                "pending_token": True,
                                                                                "render_exact": True})):
            out = tv._resume_continue({"messages": [{"role": "user", "content": "q"}]})
            again = tv._resume_continue({"messages": [{"role": "user", "content": "q"}]})
        self.assertEqual((restored, out[0], again), ([1], "body", None), "restored right before it, taken once")

    def test_restore_for_cut_restores_the_dump_into_slot_0(self):
        self.arm()
        posts = []
        ok = tv.restore_for_cut(post=lambda p, b: posts.append((p, b)) or {"n_restored": 7,
                                                                          "timings": {"restore_ms": 2.0}})
        self.assertTrue(ok)
        self.assertEqual(posts, [("/slots/0?action=restore", {"filename": "qr1.resume.bin"})])
        self.assertEqual(tv.RESUME["restored"], {"restored_tokens": 7, "restore_ms": 2.0, "armed": None})

    def test_a_prefix_only_dump_restores_then_asks_as_usual(self):
        self.arm()
        self.write_dump(cut=True)
        tv.prepare_resume()
        tv.RESUME["cut"].update(key=tv.request_key({"messages": []}), continuable=False)
        with mock.patch.object(tv, "restore_for_cut", return_value=True) as r, \
             mock.patch.object(tv, "continue_for") as c:
            self.assertIsNone(tv._resume_continue({"messages": []}))
        r.assert_called_once()
        c.assert_not_called()


class ContinueFor(Base):
    def post(self, gen_text=None):
        def post(path, b):
            if path == "/apply-template":
                last = b["messages"][-1]
                if last["role"] != "assistant":
                    return {"prompt": "PROMPT"}
                return {"prompt": "PROMPT:" + last.get("reasoning_content", "") + last.get("content", "")}
            if path == "/tokenize":
                tail = b["content"][len("PROMPT"):].lstrip(":")
                return {"tokens": [1, 2, 3] + [CH[c] for c in tail]}
            if path == "/detokenize":
                return {"content": gen_text if gen_text is not None else "".join(INV.get(t, "?") for t in b["tokens"])}
            raise AssertionError(path)
        return post

    def test_the_cut_call_continues_from_the_slot_ids_and_the_pending_token(self):
        self.arm()
        body = {"messages": [{"role": "user", "content": "q"}], "max_tokens": -1, "seed": 1}
        out = tv.continue_for(body, {"pending_token": 13, "pre_wall_s": 9.0}, post=self.post(),
                              read_slot=lambda: [1, 2, 3, 11, 12])
        cbody, pre_r, pre_c, info = out
        self.assertEqual(cbody["messages"][-1], {"role": "assistant", "content": "abc"})
        self.assertEqual((cbody["continue_final_message"], cbody["add_generation_prompt"], cbody["max_tokens"]),
                         ("content", False, -1))
        self.assertEqual((pre_c, info["continued_from"], info["pending_token"], info["render_exact"]),
                         ("abc", 3, True, True))
        capped = tv.continue_for(dict(body, max_tokens=10), {"pending_token": 13}, post=self.post(),
                                 read_slot=lambda: [1, 2, 3, 11, 12])
        self.assertEqual(capped[0]["max_tokens"], 7, "a per-call cap counts the tokens already written")
        self.assertNotIn("lab_resume", cbody, "no armed restore (an engine without patch #3): the continue as before")
        self.assertFalse(info["lab_resume"])

    def test_an_armed_restore_has_the_engine_take_the_next_step(self):
        self.arm()
        body = {"messages": [{"role": "user", "content": "q"}], "max_tokens": -1}
        tv.RESUME["restored"] = {"restored_tokens": 5, "restore_ms": 1.0, "armed": True}
        cbody, _, _, info = tv.continue_for(body, {"pending_token": 13}, post=self.post(),
                                            read_slot=lambda: [1, 2, 3, 11, 12])
        self.assertIs(cbody["lab_resume"], True)
        self.assertTrue(info["lab_resume"])
        unarmed = dict(tv.RESUME["restored"], armed=False)                   # e.g. the save landed mid-replay
        tv.RESUME["restored"] = unarmed
        self.assertNotIn("lab_resume", tv.continue_for(body, {"pending_token": 13}, post=self.post(),
                                                       read_slot=lambda: [1, 2, 3, 11, 12])[0])
        tv.RESUME["restored"] = dict(unarmed, armed=True)
        self.assertNotIn("lab_resume", tv.continue_for(body, {}, post=self.post(), read_slot=lambda: [1, 2, 3, 11, 12])[0],
                         "no pending token: the prompt cannot be the saved state + it")

    def test_a_slot_that_is_not_this_calls_falls_back_to_asking(self):
        self.arm()
        self.assertIsNone(tv.continue_for({"messages": [{"role": "user", "content": "q"}]}, {"pending_token": 13},
                                          post=self.post(), read_slot=lambda: [9, 9, 9, 11]))

    def test_a_half_answer_that_opened_a_tool_call_is_not_continued(self):
        self.arm()
        self.assertIsNone(tv.continue_for({"messages": [{"role": "user", "content": "q"}]}, {"pending_token": 13},
                                          post=self.post(gen_text="ab<tool_call>{\"na"),
                                          read_slot=lambda: [1, 2, 3, 11, 12]))


class CompleteAndChat(Base):
    def test_complete_continues_the_restored_cut_call_and_prices_it_without_the_restart(self):
        self.arm()
        orig = {"messages": [{"role": "user", "content": "q"}], "temperature": 0, "seed": 1}
        tv.RESUME["cut"] = {"key": tv.request_key(orig), "pending_token": 13, "continuable": True}
        cbody = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "pre"}],
                 "continue_final_message": "content", "add_generation_prompt": False}
        info = {"continued_from": 130, "pending_token": True, "render_exact": True, "pre_wall_s": 20.0}
        sent = []

        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode()))
            return FakeResp([{"choices": [{"delta": {"content": " rest"}}]},
                             {"choices": [], "timings": {"predicted_n": 5, "prompt_n": 1}}])
        with mock.patch.object(tv, "continue_for", return_value=(cbody, "thought ", "pre", info)), \
             mock.patch.object(tv, "restore_for_cut", return_value=True) as restore, \
             mock.patch.object(tv.urllib.request, "urlopen", side_effect=urlopen):
            text, wall, tm = tv.complete("q", 1, 0)
        restore.assert_called_once()
        self.assertEqual(sent[0], cbody, "the continue is what went to the server")
        self.assertEqual((text, tm["thinking"], tm["gen_tokens"]), ("pre rest", "thought ", 135))
        self.assertGreaterEqual(wall, 20.0, "the price is the time before the stop + after, not the restart")
        self.assertEqual(tm["resumed_from_dump"]["continued_from"], 130)

    def test_a_stop_in_complete_dumps_with_the_original_request(self):
        self.arm()
        ev = threading.Event()
        dumped = []
        with mock.patch.object(tv.urllib.request, "urlopen",
                               return_value=FakeResp([{"choices": [{"delta": {"content": "half"}}]},
                                                      {"choices": [{"delta": {"content": " more"}}]}],
                                                     after_first=ev.set)), \
             mock.patch.object(tv, "dump_on_stop", side_effect=lambda *a, **k: dumped.append((a, k))):
            text, wall, tm = tv.complete("q", 1, 0, stop_event=ev)
        self.assertEqual(tm["finish_reason"], "stopped")
        (body, reasoning, content, tool_called), kw = dumped[0]
        self.assertEqual((body["messages"], body["seed"], content, tool_called),
                         ([{"role": "user", "content": "q"}], 1, "half", False))

    def test_a_stop_in_chat_after_a_tool_call_started_dumps_the_prefix_only(self):
        self.arm()
        ev = threading.Event()
        dumped = []
        first = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "run", "arguments": "{"}}]}}]}
        with mock.patch.object(tv.urllib.request, "urlopen",
                               return_value=FakeResp([first, {"choices": [{"delta": {"content": "x"}}]}],
                                                     after_first=ev.set)), \
             mock.patch.object(tv, "dump_on_stop", side_effect=lambda *a, **k: dumped.append(a)):
            res = tv.chat([{"role": "user", "content": "q"}], tools=[{"type": "function"}], stop_event=ev)
        self.assertEqual(res["done_reason"], "stopped")
        self.assertTrue(dumped[0][3], "a cut inside a tool call: the dump keeps the prefix, never the call")


class RunArmWiring(Base):
    def run_arm(self, complete_fn, arm="rigby", stop_event=None):
        seen = {}

        def start(*a, **k):
            seen.update(k)
            return "proc", 1.0
        with mock.patch.object(tv, "start_server", side_effect=start), mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", side_effect=complete_fn), \
             mock.patch.object(tv, "start_backends", side_effect=lambda h, log: list(h)), \
             mock.patch.object(tv, "kill_backends", return_value=[]), \
             mock.patch.object(tv, "queued_names", return_value=set()), \
             mock.patch.object(tv, "_http_json", return_value={"n_restored": 5, "timings": {"restore_ms": 1.0}}):
            for f in os.listdir(tf.STATE_DIR):
                if ".checkpoint.jsonl" in f:
                    os.remove(os.path.join(tf.STATE_DIR, f))
            rows, meta = tv.run_arm(arm, dict(SPEC), fake_payload(), "/tmp", ["frodo"] if arm == "voltron" else [],
                                    lambda m: None, lambda *a: None, stop_event=stop_event,
                                    adb_sh=lambda *a, **k: "")
        return seen, rows, meta

    def test_the_rigby_arm_saves_slots_accepts_its_dump_without_touching_slot_0_and_disarms(self):
        launch = tv.resume_launch(dict(SPEC), 2048)
        self.write_dump(launch=launch, cut=False)
        seen, rows, meta = self.run_arm(lambda *a, **k: (GOOD, 1.0, {}))
        self.assertEqual(seen["slot_save_path"], tv.slots_dir())
        self.assertEqual((meta["quick_resume"]["saved_tokens"], meta["quick_resume"]["continuable"]), (5, False))
        self.assertNotIn("restored_tokens", meta["quick_resume"], "no restore before the cut call is asked")
        self.assertIsNone(tv.RESUME["name"], "disarmed on every route")
        self.assertFalse(os.path.exists(tv._resume_files("qr1")[1]), "a finished order retires its spent dump")

    def test_a_stopped_order_that_dumped_keeps_its_dump_for_the_requeue(self):
        b, j = self.write_dump(launch={"other": 1})       # a stale dump: retired at the restore
        tv.RESUME["dumped"] = False

        def cut(*a, **k):
            b2, j2 = tv._resume_files("qr1")
            with open(j2, "w", encoding="utf-8") as f:
                json.dump({"launch": {}, "cut": None}, f)
            tv.RESUME["dumped"] = True                     # dump_on_stop's mark, as complete() makes it on a stop
            return "", 1.0, {"finish_reason": "stopped"}
        seen, rows, meta = self.run_arm(cut)
        self.assertTrue(meta.get("stopped"))
        self.assertTrue(os.path.exists(tv._resume_files("qr1")[1]), "the requeue continues from it")

    def test_the_voltron_arm_never_arms_the_quick_resume(self):
        seen, rows, meta = self.run_arm(lambda *a, **k: (GOOD, 1.0, {}), arm="voltron")
        self.assertIsNone(seen.get("slot_save_path"))
        self.assertNotIn("quick_resume", meta)


if __name__ == "__main__":
    unittest.main()
