"""Regression tests for the deploy failure where a resumed job overflowed the window, dropped the task prompt, and generated spurious cleanup orders.

Specifically verifies:
1. Job claims are released on process restart.
2. Prompt size is validated before sending.
3. Summarization handles text exceeding the window size.
4. Redundant cleanup orders for in-progress work are rejected."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import cancellation  # noqa: E402
import code_cleanup  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402
import scheduler  # noqa: E402
import tools  # noqa: E402

REFUSED = 'window hai-win: HTTP 400: {"error":"request (107007 tokens) exceeds the available context size (65536 tokens)"}'


class ARestartReleasesThePreviousProcesssJobClaims(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(scheduler, "CLAIM_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(scheduler, "render_busy", lambda host: (False, ""))
        p.start()
        self.addCleanup(p.stop)

    def test_the_jobs_own_leftover_no_longer_holds_the_lead(self):
        crown = "qwen3.8-flash-next-strata-q2_0"
        scheduler.claim(scheduler.LEAD_BOX, scheduler.LIVE, "hai-job:ClaudeSupervisor", note=crown)
        url = scheduler.CARDS[scheduler.LEAD_BOX]["url"]
        self.assertEqual(scheduler.occupied_for(url, config.HEAVY_MODEL, takes_window=True)["holder"],
                         "hai-job:ClaudeSupervisor", "positive control: this is what read as held")
        app._release_previous_kirks_job_claims()
        self.assertIsNone(scheduler.occupied_for(url, config.HEAVY_MODEL, takes_window=True))

    def test_a_chat_claim_and_a_factory_claim_stay(self):
        scheduler.claim(scheduler.BRAIN, scheduler.LIVE, "hai-chat:Alex", note="qwen3.5:9b")
        scheduler.claim(scheduler.FAILOVER, "gpu", "a factory order")
        app._release_previous_kirks_job_claims()
        self.assertEqual(scheduler.active_claim(scheduler.BRAIN, scheduler.LIVE)["holder"], "hai-chat:Alex")
        self.assertEqual(scheduler.active_claim(scheduler.FAILOVER, "gpu")["holder"], "a factory order")

    def test_it_runs_before_any_run_resumes(self):
        src = open(app.__file__, encoding="utf-8").read()
        tail = src[src.rindex('if not os.getenv("HAI_NOTES_DIR"):'):]
        self.assertLess(tail.index("_release_previous_kirks_job_claims()"), tail.index("_resume_interrupted_runs"))


class WeighingAPrompt(unittest.TestCase):
    def test_the_measured_conversation_weighs_what_the_engine_said(self):
        got = ollama_client._estimated_tokens([{"role": "user", "content": "x" * 337493}])
        self.assertLess(abs(got - 107007) / 107007, 0.01, got)

    def test_tool_calls_and_schemas_count(self):
        bare = ollama_client._estimated_tokens([{"role": "assistant", "content": "a"}])
        call = {"role": "assistant", "content": "a", "tool_calls": [{"function": {"name": "f", "arguments": {"c": "y" * 3150}}}]}
        self.assertGreater(ollama_client._estimated_tokens([call]), bare + 900)
        self.assertGreater(ollama_client._estimated_tokens([{"role": "assistant", "content": "a"}],
                                                           [{"schema": "z" * 3150}]), bare + 900)


class SummarisingMoreThanTheWindow(unittest.TestCase):
    def fake(self, seen, refuse_over=None, reply=lambda text: "S%d" % len(text)):
        def _once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
            text = messages[-1]["content"]
            if refuse_over is not None and len(text) > refuse_over:
                raise RuntimeError(REFUSED)
            seen.append(text)
            return {"message": {"content": reply(text)}}
        return mock.patch.object(ollama_client, "_chat_once", _once)

    def test_the_incident_size_fits_every_request_and_loses_nothing(self):
        paragraphs = ["paragraph %06d " % i + "w" * 300 for i in range(1100)]
        text = "\n\n".join(paragraphs)
        self.assertGreater(len(text), 337000)
        seen = []
        with self.fake(seen):
            out = ollama_client._summarise_to_fit("h", "m", text, 65536, "5m", 10)
        limit = 65536 * ollama_client.COMPACT_CHARS_PER_TOKEN / 2
        self.assertGreater(len(seen), 1, "it went in pieces")
        self.assertTrue(all(len(t) <= limit for t in seen), "no request above half the window")
        sent = "\n\n".join(seen)
        self.assertEqual([p for p in paragraphs if p in sent], paragraphs, "every paragraph reached the model, in order")
        self.assertTrue(out)

    def test_a_text_that_fits_is_one_request(self):
        seen = []
        with self.fake(seen):
            self.assertEqual(ollama_client._summarise_to_fit("h", "m", "small", 1000, "5m", 10), "S5")
        self.assertEqual(seen, ["small"])

    def test_an_engine_that_refuses_the_size_gets_halves(self):
        seen = []
        text = "a" * 4000 + "\n\n" + "b" * 4000
        with self.fake(seen, refuse_over=5000):
            out = ollama_client._summarise_to_fit("h", "m", text, 1000000, "5m", 10)
        self.assertEqual(seen, ["a" * 4000, "b" * 4000])
        self.assertEqual(out, "S4000\n\nS4000")

    def test_any_other_failure_is_raised_not_split(self):
        def _once(*a, **k):
            raise RuntimeError("HTTP 500: engine went away")
        with mock.patch.object(ollama_client, "_chat_once", _once):
            with self.assertRaises(RuntimeError):
                ollama_client._summarise_to_fit("h", "m", "text", 1000, "5m", 10)

    def test_a_stop_lands(self):
        def _once(*a, **k):
            raise cancellation.Cancelled()
        with mock.patch.object(ollama_client, "_chat_once", _once):
            with self.assertRaises(cancellation.Cancelled):
                ollama_client._summarise_to_fit("h", "m", "text", 1000, "5m", 10)

    def test_summaries_still_too_large_are_summarised_again_and_a_pass_that_cannot_shrink_stops(self):
        seen = []
        text = "\n\n".join("p" * 3000 for _ in range(20))
        # long summaries on the first pass, short once they are summaries of summaries
        with self.fake(seen, reply=lambda t: "s" * (2000 if t.startswith("p") else 50)):
            out = ollama_client._summarise_to_fit("h", "m", text, 2000, "5m", 10)
        self.assertLessEqual(len(out), 2000 * ollama_client.COMPACT_CHARS_PER_TOKEN / 2)
        seen = []
        with self.fake(seen, reply=lambda t: t + t):      # a model that only grows it ends instead of looping
            out = ollama_client._summarise_to_fit("h", "m", text, 2000, "5m", 10)
        self.assertTrue(out)
        self.assertLess(len(seen), 200)


class AJobIsWeighedBeforeItIsSent(unittest.TestCase):
    BRIEF = "Design a 3D-printable cookie cutter. Deliver a print-ready STL file."

    def setUp(self):
        for target, name, val in ((ollama_client, "_crown", lambda: {}),
                                  (ollama_client, "can_think", lambda host, model: False),
                                  (ollama_client.tools, "tools_for", lambda tl, u: tl)):
            p = mock.patch.object(target, name, val)
            p.start()
            self.addCleanup(p.stop)

    def conversation(self, size):
        return [{"role": "system", "content": "sys"}, {"role": "user", "content": self.BRIEF},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "coding_read", "arguments": {}}}]},
                {"role": "tool", "content": "r" * size},
                {"role": "assistant", "content": "next", "tool_calls": [{"function": {"name": "coding_read", "arguments": {}}}]},
                {"role": "tool", "content": "ok"}]

    def run_chat(self, msgs, job):
        calls = []

        def _once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
            calls.append([dict(m) for m in messages])
            summary = messages[0]["content"] == ollama_client._COMPACT_SYSTEM
            return {"message": {"role": "assistant", "content": "summary" if summary else "an answer long enough"},
                    "prompt_eval_count": 100, "eval_count": 10}
        with mock.patch.object(ollama_client, "_chat_once", _once):
            with ollama_client.call_context(job=job):
                ollama_client.chat(config.HEAVY_HOST, "m", msgs, [], 10000)
        return calls

    def test_an_oversize_job_compacts_first_and_its_brief_reaches_the_model(self):
        calls = self.run_chat(self.conversation(100000), job=True)
        self.assertEqual(calls[0][0]["content"], ollama_client._COMPACT_SYSTEM, "the first request is the summary")
        turn = next(c for c in calls if c[0]["content"] != ollama_client._COMPACT_SYSTEM)
        self.assertIn(self.BRIEF, [m.get("content") for m in turn], "the task survives")
        self.assertLess(ollama_client._estimated_tokens(turn), 9000, "the turn that is sent fits the window")

    def test_a_job_that_fits_is_left_alone(self):
        calls = self.run_chat(self.conversation(100), job=True)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0][0]["content"], ollama_client._COMPACT_SYSTEM)

    def test_a_plain_chats_first_call_is_left_to_the_chat_path(self):
        calls = self.run_chat(self.conversation(100000), job=False)
        self.assertNotEqual(calls[0][0]["content"], ollama_client._COMPACT_SYSTEM)


class ACleanupOrderInProgressIsNotSubmittedAgain(unittest.TestCase):
    NAME = "s30-code-orchestrator"

    def test_what_counts_as_in_progress(self):
        for status in ("pending", "held", "claimed", "running"):
            job = {"order_name": self.NAME + "_mothership", "status": status, "id": "a"}
            self.assertIs(code_cleanup.order_in_flight(self.NAME, [job]), job, status)
        for job in ({"order_name": self.NAME + "_mothership", "status": "done"},
                    {"order_name": self.NAME + "_mothership", "status": "failed"},
                    {"order_name": self.NAME + "-moverework_mothership", "status": "running"},
                    {"status": "running"}):
            self.assertIsNone(code_cleanup.order_in_flight(self.NAME, [job]), job)

    def test_the_tool_refuses_at_the_door_before_touching_the_staged_work(self):
        with mock.patch.object(code_cleanup, "order_in_flight", return_value={"id": "ab56", "status": "running"}), \
                mock.patch.object(code_cleanup, "build_code_work") as build, \
                mock.patch.object(code_cleanup, "submit_code_order") as submit:
            out = tools._code_propose_cleanup_tool({"mirror": "orchestrator"}, "ClaudeSupervisor")
        self.assertIn("already in progress", out)
        self.assertIn("ab56", out)
        build.assert_not_called()
        submit.assert_not_called()

    def test_with_nothing_in_progress_it_submits(self):
        info = {"name": self.NAME, "candidates": 1, "files": 1}
        with mock.patch.object(code_cleanup, "order_in_flight", return_value=None), \
                mock.patch.object(code_cleanup, "build_code_work", return_value=info), \
                mock.patch.object(code_cleanup, "submit_code_order", return_value={"placed": [], "drafts_unc": "x"}) as submit:
            out = tools._code_propose_cleanup_tool({"mirror": "orchestrator"}, "ClaudeSupervisor")
        submit.assert_called_once()
        self.assertIn("order submitted", out)

    def test_a_rerun_keeps_the_prior_work_and_drafts_under_one_stamp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        code_cleanup._write_work(tmp, self.NAME, {"v": 1})
        with open(os.path.join(tmp, self.NAME + ".drafts.json"), "w", encoding="utf-8") as f:
            json.dump({"d": 1}, f)
        code_cleanup._write_work(tmp, self.NAME, {"v": 2})
        names = sorted(os.listdir(tmp))
        kept = [n for n in names if ".superseded-" in n]
        self.assertEqual(len(kept), 2, names)
        self.assertEqual({n.rsplit("-", 1)[1] for n in kept}.__len__(), 1, "one stamp names one generation")
        with open(os.path.join(tmp, self.NAME + ".work.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 2})
        with open(os.path.join(tmp, next(n for n in kept if ".work." in n)), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 1})


if __name__ == "__main__":
    unittest.main()
