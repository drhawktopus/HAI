"""Verify that a live state dump costs no extra steps when a deploy pauses mid-order. The probe uses `run_arm`'s knobs because it runs on the production launch path."""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tablet_foundry as TF
import tablet_runner as TR
import tolkien_llm as tv
from test_tablet_slips import SlipTestBase

PROBE = {"kind": "llm", "name": "dump1", "model": "Qwen3.6-35B-A3B-Q4_K_M.gguf", "arms": ["rigby"], "ctx": 65536,
         "reasoning": "on", "gpu": "gtx1060", "fit_target": 512, "build": "cuda", "parallel": 1, "ubatch": 2048,
         "spec_type": "draft-mtp", "dump_probe": {"p": 30000, "cut": 64, "n": 32}}


class Validation(unittest.TestCase):
    def test_a_probe_needs_no_payload_or_tasks_and_names_its_three_sizes(self):
        self.assertEqual(tv.llm_defects(PROBE), [])
        self.assertTrue(tv.dump_probe_defects({"p": 30000, "cut": 64}, 65536))
        self.assertTrue(tv.dump_probe_defects({"p": 30000, "cut": 0, "n": 32}, 65536))
        self.assertTrue(tv.dump_probe_defects({"p": 65530, "cut": 64, "n": 32}, 65536), "must fit the context")

    def test_a_probe_is_rigby_alone_and_draft_free(self):
        self.assertTrue(any("rigby alone" in x for x in tv.llm_defects(dict(PROBE, arms=["rigby", "voltron"]))))
        self.assertTrue(any("draft" in x for x in tv.llm_defects(dict(PROBE, draft_model="small.gguf"))))

    def test_an_ordinary_llm_order_still_needs_its_payload_and_tasks(self):
        plain = {k: v for k, v in PROBE.items() if k != "dump_probe"}
        d = tv.llm_defects(plain)
        self.assertTrue(any("payload" in x for x in d) and any("tasks" in x for x in d), d)


class OneLaunch(unittest.TestCase):
    def test_the_probe_launches_rigby_exactly_as_run_arm_does(self):
        """One derivation of how rigby serves its model: the probe's knobs are the ones run_arm hands start_server."""
        seen = {}

        def capture(*a, **k):
            seen.update(k)
            raise RuntimeError("captured")
        spec = {k: v for k, v in PROBE.items() if k != "dump_probe"}
        mod = mock.MagicMock(spec=["CONTEXT", "TEMPERATURE"])
        with mock.patch.object(tv, "start_server", side_effect=capture), mock.patch.object(tv, "is_agentic",
                                                                                          return_value=False):
            try:
                tv.run_arm("rigby", dict(spec, payload="/p.py", tasks=["t"]), mod, "/w", [], lambda *a: None,
                           lambda *a: None)
            except Exception:                                                # noqa: BLE001
                pass
        self.assertTrue(seen, "run_arm never reached start_server")
        mine = tv.rigby_server_kwargs(spec)
        self.assertEqual({k: seen.get(k) for k in mine}, mine)


class FiveSteps(SlipTestBase):
    def _server(self, resume_prompt_n=1, control_prompt_n=30000):
        calls = []

        def post(path, body):
            calls.append((path, dict(body)))
            if path == "/tokenize":
                return {"tokens": [1, 2, 3, 4, 5]}
            if path.startswith("/slots/0?action=save"):
                return {"n_saved": 29999, "n_written": 1234, "timings": {"save_ms": 900.0}}
            if path.startswith("/slots/0?action=restore"):
                return {"n_restored": 29999, "timings": {"restore_ms": 400.0}}
            if path.startswith("/slots/0?action=erase"):
                return {}
            n = len(body["prompt"])
            k = sum(1 for c in calls if c[0] == "/completion")
            prompt_n = {1: n, 2: 120, 3: resume_prompt_n, 4: control_prompt_n}[k]
            return {"content": "x" * body["n_predict"], "timings": {"prompt_n": prompt_n, "prompt_ms": prompt_n * 10.0,
                                                                     "predicted_n": body["n_predict"]}}
        return calls, post

    def test_cut_answer_then_dump_restart_restore_resume_and_control(self):
        calls, post = self._server()
        launched, halted = [], []
        m = tv.run_dump_probe(PROBE, lambda *a: None, post=post,
                              launch=lambda: (launched.append(1) or "proc%d" % len(launched), 30.0),
                              halt=halted.append)
        comps = [b for p, b in calls if p == "/completion"]
        self.assertEqual([len(b["prompt"]) for b in comps], [30000, 29999, 30000, 30000])
        self.assertEqual([b["n_predict"] for b in comps], [64, 0, 32, 32], "cut, nothing, resume, control")
        self.assertEqual(len(launched), 2, "the deploy: the server comes back the same way")
        self.assertIn("proc1", halted)
        self.assertEqual(m["steps_done"], 5)
        self.assertTrue(m["dump_resumes"])
        self.assertEqual(m["with_dump_ms"], 1200.0 + 900.0 + 400.0 + 10.0)
        self.assertEqual(m["without_dump_ms"], 300000.0)
        self.assertTrue(m["identical"])
        self.assertFalse(os.path.exists(os.path.join(TF.STATE_DIR, "slots", tv.DUMP_PROBE_STATE)))

    def test_a_stop_slip_between_steps_ends_the_probe_and_the_server(self):
        calls, post = self._server()
        ev = threading.Event()
        halted = []

        def post_then_stop(path, body):
            r = post(path, body)
            if path.startswith("/slots/0?action=save"):
                ev.set()
            return r
        m = tv.run_dump_probe(PROBE, lambda *a: None, stop_event=ev, post=post_then_stop,
                              launch=lambda: ("proc", 30.0), halt=halted.append)
        self.assertEqual(m["steps_done"], 2)
        self.assertIn("restore", m["stopped"])
        self.assertIn("proc", halted)

    def _continue_server(self, continue_tokens, control_tokens, ref=(11, 12, 13, 14, 15, 16)):
        """A fake llama-server for the quick-resume probe: the reference writes `ref`; the interrupted run writes its
        first `cut`; the continue and the control write what the test says."""
        calls = []
        seen = {"comp": 0}

        def post(path, body):
            calls.append((path, dict(body)))
            if path == "/tokenize":
                return {"tokens": [1, 2, 3, 4, 5]}
            if "action=save" in path:
                return {"n_saved": 10, "n_written": 99, "timings": {"save_ms": 5.0}}
            if "action=restore" in path:
                return {"n_restored": 10, "timings": {"restore_ms": 4.0}}
            if "action=erase" in path:
                return {}
            seen["comp"] += 1
            k = seen["comp"]
            toks = {1: list(ref), 2: list(ref[:body["n_predict"]]), 3: list(continue_tokens),
                    4: list(control_tokens)}[k]
            prompt_n = {1: len(body["prompt"]), 2: len(body["prompt"]), 3: 1, 4: len(body["prompt"])}[k]
            return {"tokens": toks, "content": "x", "timings": {"prompt_n": prompt_n, "prompt_ms": 1.0,
                                                                 "predicted_n": len(toks)}}
        return calls, post

    def _continue(self, cont, ctl, **dp):
        spec = dict(PROBE, dump_probe=dict({"p": 8, "cut": 2, "n": 6, "mode": "continue"}, **dp))
        calls, post = self._continue_server(cont, ctl)
        launched, halted = [], []
        m = tv.run_dump_probe(spec, lambda *a: None, post=post,
                              launch=lambda: (launched.append(1) or "proc%d" % len(launched), 1.0),
                              halt=halted.append)
        return m, calls, launched, halted

    def test_quick_resume_continues_the_half_answer_and_says_whether_it_is_lossless(self):
        m, calls, launched, halted = self._continue([13, 14, 15, 16], [13, 14, 15, 16])
        comps = [b for p, b in calls if p == "/completion"]
        self.assertEqual([len(b["prompt"]) for b in comps], [8, 8, 10, 10], "the continue prompt = question + cut")
        self.assertEqual(comps[2]["prompt"][8:], [11, 12], "continued from the EXACT tokens written before the stop")
        self.assertTrue(all(b["return_tokens"] for b in comps), "exact ids, never a text round trip")
        self.assertEqual((m["lossless"], m["first_diff"], m["prefix_identical"]), (True, None, True))
        self.assertEqual((m["continue_reread_tokens"], m["steps_done"], m["token_source"]), (1, 5, "server"))
        self.assertEqual(len(launched), 2)
        self.assertEqual(halted.count("proc2"), 1, "the relaunched server is stopped at the end, once")
        self.assertNotIn("_proc", m)

    def test_a_resume_that_drifts_names_the_first_different_token(self):
        m, *_ = self._continue([13, 99, 15, 16], [13, 14, 15, 16], temperature=0.6, seed=7)
        self.assertEqual((m["lossless"], m["first_diff"]), (False, 3))
        self.assertTrue(m["control_lossless"])
        self.assertEqual((m["temperature"], m["seed"]), (0.6, 7))

    def test_near_ties_count_the_positions_that_could_move_and_unknown_is_not_zero(self):
        import math
        lp = math.log
        r = {"completion_probabilities": [
            {"top_logprobs": [{"logprob": lp(0.98)}, {"logprob": lp(0.01)}]},     # certain: cannot move
            {"top_logprobs": [{"logprob": lp(0.48)}, {"logprob": lp(0.45)}]},     # a near-tie
            {"top_probs": [{"prob": 0.51}, {"prob": 0.47}]}]}                     # the other server shape
        self.assertEqual(tv._near_ties(r), 2)
        self.assertIsNone(tv._near_ties({}), "no probabilities = could not tell, never 'no ties'")

    def test_a_doc_probe_asks_an_open_question_about_a_real_document(self):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Runbook"))
        with open(os.path.join(d, "Runbook", "X.md"), "w", encoding="utf-8") as f:
            f.write("a real runbook")
        spec = dict(PROBE, dump_probe={"p": 8, "cut": 2, "n": 6, "mode": "continue", "doc": "Runbook/X.md"})
        calls, post = self._continue_server([13, 14, 15, 16], [13, 14, 15, 16])
        with mock.patch.object(tv, "DOCS_ROOT", d):
            m = tv.run_dump_probe(spec, lambda *a: None, post=post, launch=lambda: ("proc", 1.0), halt=lambda p: None)
        toks = [b["content"] for p, b in calls if p == "/tokenize"]
        self.assertEqual(toks, [tv.DUMP_QUESTION, "a real runbook"], "the question, then the document")
        self.assertEqual((m["doc"], m["prompt_tokens"]), ("Runbook/X.md", 8))
        self.assertTrue(all(b["n_probs"] == 2 for p, b in calls if p == "/completion"), "every call asked alike")
        self.assertIsNone(m["near_ties"], "the fake sent no probabilities: unknown, said")

    def test_continue_validation_and_a_doc_is_a_relative_library_md_path(self):
        base0 = {"p": 4000, "cut": 128, "n": 384, "mode": "continue"}
        for bad in ("/etc/passwd.md", "../x.md", "Runbook/../../x.md", "Runbook/X.txt"):
            self.assertTrue(tv.dump_probe_defects(dict(base0, doc=bad), 65536), bad)
        self.assertEqual(tv.dump_probe_defects(dict(base0, doc="Runbook/RUNBOOK.md"), 65536), [])
        base = {"p": 4000, "cut": 128, "n": 384}
        self.assertEqual(tv.dump_probe_defects(dict(base, mode="continue"), 65536), [])
        self.assertTrue(tv.dump_probe_defects(dict(base, mode="continue", cut=384), 65536), "the stop is inside")
        self.assertTrue(tv.dump_probe_defects(dict(base, mode="rewind"), 65536))
        self.assertTrue(tv.dump_probe_defects(dict(base, temperature=3), 65536))
        self.assertTrue(tv.dump_probe_defects(dict(base, extra=1), 65536))

    # ── mode "chat": the same quick resume through the chat API rigby's orders use, stopped by leaving the stream ──
    CH = {"a": 11, "b": 12, "c": 13, "d": 14, "e": 15, "z": 99}

    def _chat_server(self, render_extra=None, res=None, ctl=None, pending=None, armed=None):
        """A fake llama-server: the rendered prompt PROMPT is ids 1,2,3; each answer letter is one token (CH). The
        reference wrote abcde (the cache holds 5 of n=6); the cut run's cache holds ab. read_slot hands back each
        saved file's ids in order."""
        inv = {v: k for k, v in self.CH.items()}
        calls = []
        files = {tv.DUMP_PROBE_FILES[0]: [[1, 2, 3, 11, 12, 13, 14, 15]],
                 tv.DUMP_PROBE_STATE: [[1, 2, 3, 11, 12]],
                 tv.DUMP_PROBE_FILES[1]: res or [[1, 2, 3, 11, 12, 13, 14, 15], [1, 2, 3, 11, 12, 13, 14, 15]],
                 tv.DUMP_PROBE_FILES[2]: [ctl or [1, 2, 3, 11, 12, 13, 99, 15]]}

        def post(path, body):
            calls.append((path, json.loads(json.dumps(body))))
            if path == "/tokenize":
                text = body["content"]
                if text.startswith("PROMPT"):
                    tail = text[len("PROMPT"):].lstrip(":")
                    return {"tokens": [1, 2, 3] + [self.CH[ch] for ch in tail] + list(render_extra or [])
                            if tail else [1, 2, 3]}
                return {"tokens": [7] * 5}
            if path == "/detokenize":
                return {"content": "".join(inv.get(i, "?") for i in body["tokens"])}
            if path == "/apply-template":
                last = body["messages"][-1]
                if last["role"] != "assistant":
                    return {"prompt": "PROMPT"}
                if "content" not in last and "tool_calls" not in last:      # the engine's own rule (23b0202)
                    raise RuntimeError("llama-server HTTP 400 on /apply-template: Assistant message must contain "
                                       "either 'content' or 'tool_calls'!")
                return {"prompt": "PROMPT:" + last.get("reasoning_content", "") + last.get("content", "")}
            if "action=save" in path:
                r = {"n_saved": 5, "n_written": 99, "timings": {"save_ms": 5.0}}
                if pending is not None and body.get("filename") == tv.DUMP_PROBE_STATE:
                    r["pending_token"] = pending                # the lab-patched engine (#0)
                if armed is not None:
                    r.update(lab_drafter_bytes=777, lab_replay=not armed,    # engine patch #3
                             lab_rng_bytes=55)                              # engine patch #2
                return r
            if "action=restore" in path:
                r = {"n_restored": 5, "timings": {"restore_ms": 4.0}}
                if armed is not None:
                    r["lab_resume_armed"] = armed
                return r
            return {}
        streams = []

        def chat_stream(body, stop_after):
            streams.append((json.loads(json.dumps(body)), stop_after))
            k = len(streams)
            prompt_n = 0 if body.get("lab_resume") else {1: 3, 2: 3, 3: 1, 4: 1, 5: 5}.get(k, 1)
            return {"reasoning": "", "content": "a" if stop_after else "abcde", "chunks": 1 if stop_after else 6,
                    "timings": {"prompt_n": prompt_n, "prompt_ms": 2.0}, "probs": []}

        def read_slot(name):
            return files[name].pop(0)
        return calls, post, streams, chat_stream, read_slot

    def test_a_patched_engines_pending_token_extends_the_saved_state_by_one(self):
        m, calls, streams, launched, halted = self._chat(pending=13)
        self.assertEqual(len(streams), 4, "reference, cut, continue, control - no oracle: the engine said the token")
        cont = streams[2][0]
        self.assertEqual(cont["messages"][-1]["content"], "abc", "the half answer + the engine's pending token")
        self.assertEqual(cont["max_tokens"], 3, "n minus the 3 tokens already written")
        self.assertEqual((m["pending_token"], m["continue_with_pending"], m["pending_matches_reference"]),
                         (13, True, True))
        self.assertTrue(m["render_match"], "the render EXTENDS the saved state by exactly that token")
        self.assertEqual((m["lossless"], m["first_diff"]), (True, None))
        self.assertNotIn("oracle_lossless", m)
        self.assertIsNone(m["resume_armed"], "an engine without patch #3 says nothing - no lab resume arm")
        self.assertNotIn("resume_lossless", m)

    def test_an_engine_with_patch_3_also_takes_the_interrupted_runs_next_step(self):
        m, calls, streams, launched, halted = self._chat(pending=13, armed=True)
        self.assertEqual(len(streams), 5, "reference, cut, continue, lab resume, control")
        self.assertNotIn("lab_resume", streams[2][0], "step 4 stays the pending-token continue: one variable vs 4c")
        res = streams[3][0]
        self.assertIs(res["lab_resume"], True)
        self.assertEqual((res["messages"][-1]["content"], res["max_tokens"]), ("abc", 3),
                         "the same continue as step 4 - only the flag differs")
        restores = [p for p, _ in calls if "action=restore" in p]
        self.assertEqual(len(restores), 2, "the dump is restored again before the lab resume")
        self.assertEqual((m["resume_armed"], m["resume_reread_tokens"], m["resume_lossless"], m["resume_first_diff"]),
                         (True, 0, True, None))
        self.assertEqual((m["lab_drafter_bytes"], m["lab_replay"], m["lab_rng_bytes"]), (777, False, 55))

    def test_a_save_that_landed_mid_replay_is_not_armed_and_runs_no_lab_resume(self):
        m, calls, streams, launched, halted = self._chat(pending=13, armed=False)
        self.assertEqual(len(streams), 4, "no lab resume continue when the engine did not arm one")
        self.assertEqual((m["resume_armed"], m["lab_replay"]), (False, True))
        self.assertNotIn("resume_lossless", m)

    def _chat(self, **kw):
        spec = dict(PROBE, spec_type=None, dump_probe={"p": 8, "cut": 1, "n": 6, "mode": "chat"})
        calls, post, streams, chat_stream, read_slot = self._chat_server(**kw)
        launched, halted = [], []
        m = tv.run_dump_probe(spec, lambda *a: None, post=post,
                              launch=lambda: (launched.append(1) or "proc%d" % len(launched), 1.0),
                              halt=halted.append, chat_stream=chat_stream, read_slot=read_slot)
        return m, calls, streams, launched, halted

    def test_chat_quick_resume_reads_the_live_state_from_the_slot_and_continues_through_the_chat_api(self):
        m, calls, streams, launched, halted = self._chat()
        self.assertEqual(m["steps_done"], 5, m)
        self.assertEqual([s for _, s in streams], [None, 1, None, None, None],
                         "reference, the cut (leaves after `cut` chunks), continue, + the pending token, control")
        cont = streams[2][0]
        self.assertEqual(cont["messages"][-1], {"role": "assistant", "content": "ab"},
                         "the half answer from the SLOT's ids (the server wrote b after the client left)")
        self.assertEqual((cont["continue_final_message"], cont["add_generation_prompt"], cont["max_tokens"]),
                         ("content", False, 4))
        self.assertTrue(all(b["logprobs"] and b["top_logprobs"] == 2 for b, _ in streams), "every call asked alike")
        self.assertEqual((m["prompt_match"], m["render_match"], m["client_render_match"]), (True, True, True))
        self.assertEqual((m["server_written"], m["ran_past_client"], m["prefix_identical"]), (2, 1, True))
        self.assertEqual((m["lossless"], m["first_diff"], m["compared"]), (True, None, 5))
        self.assertEqual(streams[3][0]["messages"][-1]["content"], "abc", "the oracle adds the pending token")
        self.assertEqual((m["oracle_render_extends"], m["oracle_lossless"]), (True, True))
        self.assertEqual((m["control_lossless"], m["control_first_diff"]), (False, 3))
        self.assertEqual(len(launched), 2)
        self.assertEqual(halted.count("proc2"), 1)
        self.assertNotIn("_proc", m)

    def test_a_render_that_does_not_reproduce_the_saved_state_is_named_not_hidden(self):
        m, *_ = self._chat(render_extra=[42], res=[[1, 2, 3, 11, 12, 42, 14, 15], [1, 2, 3, 11, 12, 13, 14, 15]])
        self.assertFalse(m["render_match"])
        self.assertEqual(m["render_first_diff"], 5, "the render ran one token past the saved state")
        self.assertEqual((m["lossless"], m["first_diff"]), (False, 2))
        w = m["diff_windows"]["R_vs_target"]
        self.assertEqual((w["at"], w["x_ids"][-1], w["x"][-1]), (5, 42, "?"), "the mismatch names its own token")
        self.assertNotIn("P_vs_A", m["diff_windows"], "only pairs that differ get a window")
        self.assertEqual(m["lens"]["R"], 6)

    def test_prefill_forms_with_and_without_the_templates_tail(self):
        gp = "...<|im_start|>assistant\n<think>\n"
        self.assertEqual(tv.prefill_message(gp, "plan"),
                         ({"role": "assistant", "reasoning_content": "plan", "content": ""}, "reasoning_content"),
                         "a stop inside the thinking still sends a content key - the engine refuses one without")
        self.assertEqual(tv.prefill_message(gp, "plan", keep_tail=True)[0]["reasoning_content"], "\nplan")
        self.assertEqual(tv.prefill_message(gp, "plan</think>\n\nThe doc"),
                         ({"role": "assistant", "reasoning_content": "plan", "content": "\n\nThe doc"}, "content"))
        self.assertEqual(len(tv.prefill_candidates(gp, "plan")), 2)
        self.assertEqual(len(tv.prefill_candidates("assistant\n", "plan")), 1, "no opened block: one form")

    def test_the_prefill_kept_is_the_one_whose_render_equals_the_saved_ids(self):
        forms = [({"reasoning_content": "a"}, "reasoning_content"), ({"reasoning_content": "b"}, "reasoning_content")]
        renders = {"a": [1, 2, 9], "b": [1, 2, 3]}
        pick = tv.pick_prefill(forms, lambda mg, ct: renders[mg["reasoning_content"]], [1, 2, 3])
        self.assertEqual((pick[0], pick[3]), (1, [1, 2, 3]), "the exact render wins, whatever its order")
        renders["b"] = [1, 7, 7]
        pick = tv.pick_prefill(forms, lambda mg, ct: renders[mg["reasoning_content"]], [1, 2, 3])
        self.assertEqual(pick[0], 0, "none exact: the longest shared prefix")

    def test_prefill_without_an_opened_block(self):
        self.assertEqual(tv.prefill_message("assistant\n", "<think>x"),
                         ({"role": "assistant", "reasoning_content": "x", "content": ""}, "reasoning_content"))
        self.assertEqual(tv.prefill_message("assistant\n", "plain"), ({"role": "assistant", "content": "plain"},
                                                                      "content"))
        far = "a doc that mentions <think> early" + "x" * 200 + "assistant\n"
        self.assertEqual(tv.prefill_message(far, "plain")[1], "content", "only the generation prompt can open it")

    def _slot_file(self, head, toks, tail=b"STATE"):
        import struct
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(struct.pack("<III", *head) + struct.pack("<%di" % len(toks), *toks) + tail)
        self.addCleanup(os.remove, p)
        return p

    def test_a_slot_file_yields_its_exact_token_ids(self):
        p = self._slot_file((tv.SLOT_MAGIC, tv.SLOT_VERSION, 4), [5, 151643, 0, 77])
        self.assertEqual(tv.slot_file_tokens(p), [5, 151643, 0, 77])

    def test_the_servers_packed_token_state_is_unpacked_not_read_as_ids(self):
        """The server saves server_tokens::serialize(): [-1][version 1][count][ids][media count] - read as raw ids it
        is off by 4 and its count looks like a token that 'differs' between every pair of saves."""
        p = self._slot_file((tv.SLOT_MAGIC, tv.SLOT_VERSION, 7), [-1, 1, 3, 10, 11, 12, 0])
        self.assertEqual(tv.slot_file_tokens(p), [10, 11, 12])
        with self.assertRaises(ValueError):           # a media slot is not a text slot
            tv.slot_file_tokens(self._slot_file((tv.SLOT_MAGIC, tv.SLOT_VERSION, 7), [-1, 1, 3, 10, 11, 12, 1]))
        with self.assertRaises(ValueError):           # a packed-state version this code does not know
            tv.slot_file_tokens(self._slot_file((tv.SLOT_MAGIC, tv.SLOT_VERSION, 7), [-1, 2, 3, 10, 11, 12, 0]))

    def test_a_file_that_is_not_this_engines_slot_file_raises(self):
        with self.assertRaises(ValueError):
            tv.slot_file_tokens(self._slot_file((0x12345678, tv.SLOT_VERSION, 1), [1]))
        with self.assertRaises(ValueError):
            tv.slot_file_tokens(self._slot_file((tv.SLOT_MAGIC, 3, 1), [1]))

    def test_a_truncated_slot_file_raises(self):
        with self.assertRaises(ValueError):
            tv.slot_file_tokens(self._slot_file((tv.SLOT_MAGIC, tv.SLOT_VERSION, 9), [1, 2], tail=b""))

    def test_a_refusal_mid_probe_keeps_the_steps_it_finished_and_names_the_servers_reason(self):
        calls, post, streams, chat_stream, read_slot = self._chat_server()

        def refuse_the_prefill(path, body):
            if path == "/apply-template" and body["messages"][-1]["role"] == "assistant":
                raise RuntimeError("llama-server HTTP 400 on /apply-template: a reason the server gave")
            return post(path, body)
        spec = dict(PROBE, spec_type=None, dump_probe={"p": 8, "cut": 1, "n": 6, "mode": "chat"})
        halted = []
        m = tv.run_dump_probe(spec, lambda *a: None, post=refuse_the_prefill, launch=lambda: ("proc", 1.0),
                              halt=halted.append, chat_stream=chat_stream, read_slot=read_slot)
        self.assertEqual(m["steps_done"], 2, "the reference and the cut were measured - they ship")
        self.assertIn("a reason the server gave", m["error"])
        self.assertEqual((m["server_written"], m["prefix_identical"]), (2, True))
        self.assertEqual(halted, ["proc"], "the server is still stopped")
        sent = []
        with mock.patch.object(TR, "notify_desk", side_effect=sent.append):
            out = self._ship(m)
        self.assertIn("a reason the server gave", json.dumps(out["errors"]))
        self.assertEqual(out["rows"][0]["steps_done"], 2)

    def test_a_server_refusal_carries_the_servers_own_reason(self):
        import io
        import urllib.error
        body = b'{"error":{"code":400,"message":"Assistant message must contain either content"}}'
        err = urllib.error.HTTPError("http://x/apply-template", 400, "Bad Request", {}, io.BytesIO(body))
        with mock.patch.object(tv.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                tv._http_json(1, "/apply-template", {"messages": []})
        self.assertIn("Assistant message must contain either content", str(cm.exception))
        self.assertIn("400", str(cm.exception))

    def test_chat_validation_puts_the_stop_inside_the_answer(self):
        base = {"p": 4000, "cut": 128, "n": 384, "mode": "chat"}
        self.assertEqual(tv.dump_probe_defects(base, 65536), [])
        self.assertTrue(tv.dump_probe_defects(dict(base, cut=384), 65536))

    def test_a_chat_probe_reports_render_lossless_and_the_pending_token_to_the_desk(self):
        sent = []
        with mock.patch.object(TR, "notify_desk", side_effect=sent.append):
            self._ship({"mode": "chat", "steps_done": 5, "control_read_tokens": 30000, "render_match": True,
                        "lossless": False, "first_diff": 0, "oracle_lossless": True, "oracle_reread_tokens": 1,
                        "ran_past_client": 2})
        self.assertTrue(sent and "chat quick-resume" in sent[-1], sent)
        self.assertIn("pending token: lossless True (re-read 1)", sent[-1])

    def _ship(self, metrics):
        p = self.queue("dump1", PROBE)
        with mock.patch.object(tv, "run_dump_probe", return_value=metrics):
            res = TR.build_dump_probe_order(p, PROBE)
        with open(res, encoding="utf-8") as f:
            return json.load(f)

    def test_the_order_ships_usable_only_when_its_control_reread_the_whole_question(self):
        good = {"steps_done": 5, "control_read_tokens": 30000, "resume_reread_tokens": 1}
        out = self._ship(good)
        self.assertTrue(out["usable"])
        self.assertEqual(out["rows"][0]["task"], "dump_probe")
        cached = self._ship(dict(good, control_read_tokens=40))
        self.assertTrue(cached["VOID"], "a cache-served control voids the comparison")
        self.assertIn("40 of 30000", cached["control"])
        partial = self._ship({"steps_done": 3, "control_read_tokens": None})
        self.assertTrue(partial["VOID"])


if __name__ == "__main__":
    unittest.main()
