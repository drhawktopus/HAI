"""Crown fallback is pass-over, not retry: if a crown cannot serve (e.g., input is invalid for that model), it is skipped in favor of the box's standing model. This is not a transient network error."""
import os
import sys
import tempfile
import shutil
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import chat_window as CW  # noqa: E402
import ollama_client  # noqa: E402

FN = "qwen3.8-flash-next-strata-q2_0"


class _W:
    """The two things the stream reads off a window: its folder and the desk's status for its order."""

    def __init__(self, d, status):
        self.dir, self.name, self._status = d, "hai-win-test", status

    def desk_status(self):
        return self._status


class AWindowThatFailsBeforeAnyOutputIsUnavailable(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def read(self, status):
        with mock.patch.object(CW, "PERIOD_S", CW.POLL_S):          # the desk is asked on every poll
            list(CW._Stream(_W(self.d, status), "t00001-x", None, sleep=lambda s: None))

    def test_failed_before_output_raises_unavailable(self):
        with self.assertRaises(CW.WindowFailedAtOpen) as cm:
            self.read("failed")
        self.assertTrue(getattr(cm.exception, "unavailable", False))
        self.assertIsInstance(cm.exception, CW.httpx.HTTPError, "still the error type every chat loop catches")

    def test_an_ended_window_is_still_a_plain_read_error(self):
        with self.assertRaises(CW.httpx.ReadError) as cm:
            self.read("done")
        self.assertFalse(getattr(cm.exception, "unavailable", False), "a window that served and ended is not the box")


class ACrownThatCannotServeFallsToTheStandingModel(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.p = [mock.patch.object(ollama_client, "lead_model",
                                    lambda host, model, job: FN if model == config.HEAVY_MODEL else model),
                  mock.patch.object(ollama_client, "_CALL_CONTEXT", types.SimpleNamespace(job=True, think_pin=None))]
        for x in self.p:
            x.start()
            self.addCleanup(x.stop)

    def body(self, crown_error):
        orig = ollama_client.chat.__wrapped__

        def wrapped(host, model, *a, **kw):
            self.calls.append(model)
            if model == FN and crown_error is not None:
                raise crown_error
            return ("answer from %s" % model, None)
        return mock.patch.object(ollama_client.chat, "__wrapped__", wrapped), orig

    def run_chat(self, crown_error):
        patcher, _orig = self.body(crown_error)
        # the module-level chat() delegates to its own body for the crown; the fallback runs the remaining body, so
        # stand in for _chat_once below the swap: here the standing model's answer is what the body returns
        with patcher, mock.patch.object(ollama_client, "_chat_once",
                                        side_effect=AssertionError("the body below the swap is not under test")):
            try:
                return ollama_client.chat(config.HEAVY_HOST, config.HEAVY_MODEL, [{"role": "user", "content": "x"}],
                                          [], 8192, status_callback=self.statuses.append)
            except AssertionError as e:
                return ("fell through to the standing model's own body", str(e))

    def setUpStatuses(self):
        self.statuses = []

    def test_the_crown_serves_when_it_can(self):
        self.setUpStatuses()
        out = self.run_chat(None)
        self.assertEqual(out, ("answer from %s" % FN, None))
        self.assertEqual(self.calls, [FN])

    def test_an_unavailable_crown_falls_to_the_standing_model_once(self):
        self.setUpStatuses()
        err = ollama_client.ChatError(config.HEAVY_HOST, FN, "the window is failed before any output",
                                      unavailable=True)
        out = self.run_chat(err)
        self.assertEqual(self.calls, [FN], "the crown is asked ONCE, never again")
        self.assertEqual(out, ("fell through to the standing model's own body", "the body below the swap is not under test"),
                         "the standing model's own call was reached")
        self.assertTrue(any("could not serve" in s and config.HEAVY_MODEL in s for s in self.statuses), self.statuses)

    def test_any_other_crown_failure_is_raised_not_hidden(self):
        self.setUpStatuses()
        err = ollama_client.ChatError(config.HEAVY_HOST, FN, "stream cut mid-answer")
        with self.assertRaises(ollama_client.ChatError):
            self.run_chat(err)


class TheCallSaysWhichModelServed(unittest.TestCase):
    """Telemetry must reflect the actual serving model, not the requested one."""

    def served(self, crown):
        tele, asked = {}, []

        def once(host, model, messages, *a, **k):
            asked.append(model)
            return {"message": {"role": "assistant", "content": "done"}}
        with mock.patch.object(ollama_client, "lead_model", lambda host, model, job: crown or model), \
                mock.patch.object(ollama_client, "_chat_once", once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl):
            ollama_client.chat("http://h", "standing", [{"role": "user", "content": "x"}], [], 8192, telemetry=tele)
        return tele.get("model"), asked

    def test_a_call_the_crown_served_names_the_crown(self):
        self.assertEqual(self.served(FN), (FN, [FN]))

    def test_a_call_with_no_swap_names_the_model_it_was_sent_to(self):
        self.assertEqual(self.served(None), ("standing", ["standing"]))

    def test_the_lead_paths_result_reads_the_label_back_from_the_call(self):
        """Source-level pin: the whole lead dispatch has no cheap behavioural harness (test_overnight's precedent)."""
        import inspect
        import app
        src = inspect.getsource(app._dispatch_lead)
        self.assertIn('_served_model = _pt.get("model")', src)
        self.assertIn('"model": _served_model or config.HEAVY_MODEL', src)


class _Claims(unittest.TestCase):
    """A private claim dir and a job context for user Avery; `call()` is one tracked chat call on Mothership."""

    def setUp(self):
        import scheduler
        self.s = scheduler
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for p in (mock.patch.object(scheduler, "CLAIM_DIR", self.d),
                  mock.patch.object(ollama_client, "_CALL_CONTEXT", types.SimpleNamespace(job=True, user="Avery")),
                  mock.patch.object(ollama_client, "_INFLIGHT", {}), mock.patch.object(ollama_client, "_LAST_DONE", {})):
            p.start()
            self.addCleanup(p.stop)
        self.url = scheduler.CARDS[scheduler.LEAD_BOX]["url"]

    def call(self, error=None):
        try:
            with ollama_client.track(self.url, FN):
                if error is not None:
                    raise error
        except Exception:                                        # noqa: BLE001
            pass

    def live(self):
        return self.s.active_claim(self.s.LEAD_BOX, self.s.LIVE)


class ACallThatNeverServedLeavesNoLiveClaim(_Claims):
    """The same proof, second round: the failed Flash-Next windows had refreshed 'mothership live hai-job' at every
    call's end, so for its 15-min rolling ttl the next Hard turn by the same user read the lead as held."""

    def test_an_unavailable_call_releases_its_own_claim(self):
        self.call(CW.WindowFailedAtOpen("the window is failed before any output"))
        self.assertIsNone(self.live())

    def test_a_served_call_keeps_the_box_live(self):
        self.call()
        self.assertEqual((self.live() or {}).get("note"), FN)

    def test_a_transient_failure_keeps_the_claim(self):
        self.call(CW.httpx.ReadError("stream cut"))
        self.assertEqual((self.live() or {}).get("note"), FN, "only a call the box could not serve lets go")

    def test_another_users_or_another_models_claim_is_never_released(self):
        self.s.claim(self.s.LEAD_BOX, self.s.LIVE, "hai-job:Alex", ttl=900, note=FN)
        ollama_client._unmark_live(self.url, FN)
        self.assertEqual((self.live() or {}).get("holder"), "hai-job:Alex")
        self.s.claim(self.s.LEAD_BOX, self.s.LIVE, "hai-job:Avery", ttl=900, note=config.HEAVY_MODEL)
        ollama_client._unmark_live(self.url, FN)
        self.assertEqual((self.live() or {}).get("note"), config.HEAVY_MODEL)


class AFinishedRunHoldsNoLiveClaim(_Claims):
    """The third round: a Hard turn ended and its job claim kept rolling 15 min, so the same user's next Hard turn read
    the lead as held. A run's job claims end with the run, by any route; another holder's claim, and a claim taken over
    since by another model, are left alone; a nested run never releases its parent's."""

    def test_the_runs_job_claim_is_released_when_it_ends(self):
        with ollama_client.live_job_scope():
            self.call()
            self.assertEqual((self.live() or {}).get("note"), FN, "live while the run runs")
        self.assertIsNone(self.live())

    def test_released_even_when_the_run_raises(self):
        with self.assertRaises(RuntimeError):
            with ollama_client.live_job_scope():
                self.call()
                raise RuntimeError("the turn failed")
        self.assertIsNone(self.live())

    def test_a_nested_scope_leaves_the_parents_claim(self):
        with ollama_client.live_job_scope():
            with ollama_client.live_job_scope():
                self.call()
            self.assertEqual((self.live() or {}).get("note"), FN, "the parent run is still live")
        self.assertIsNone(self.live())

    def test_a_claim_now_held_by_someone_else_is_left_alone(self):
        with ollama_client.live_job_scope():
            self.call()
            self.s.claim(self.s.LEAD_BOX, self.s.LIVE, "hai-job:Alex", ttl=900, note=FN)
        self.assertEqual((self.live() or {}).get("holder"), "hai-job:Alex")

    def test_a_plain_chat_claim_keeps_its_keep_alive(self):
        ollama_client._CALL_CONTEXT.job = False
        with ollama_client.live_job_scope():
            self.call()
        self.assertTrue(str((self.live() or {}).get("holder")).startswith("hai-chat:"), "it tracks a resident model")


if __name__ == "__main__":
    unittest.main()
