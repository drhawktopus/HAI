"""Tests for Mothership heavy-request serialization (one 27B slot at a time).

A single shared GPU holds one 27B at a time, so concurrent heavy requests would double-WoL the
box and thrash the model in/out of VRAM. `config` defaults are checked directly; the call-site
wiring is verified statically (`app` pulls heavy deps not present in the bare runner); the
lock/slot behavior is exercised behaviorally when `app` can be imported (the in-container suite)
and skipped otherwise."""
import threading
import time
import unittest
from pathlib import Path

import config

APP = Path("app.py").read_text(encoding="utf-8")

try:  # app imports fastapi/etc — present in the container, absent in the bare runner.
    import app as _app
    _APP_IMPORTABLE = True
except Exception:
    _app = None
    _APP_IMPORTABLE = False


class SerializationConfigTests(unittest.TestCase):
    def test_the_queue_wait_has_no_clock(self):
        # The queue wait must not be time-bounded; a request leaves on its turn or a Stop.
        self.assertFalse(hasattr(config, "MOTHERSHIP_QUEUE_TIMEOUT"))


class SerializationWiringTests(unittest.TestCase):
    def test_lock_and_slot_defined(self):
        # _LeadSlot enforces quick-request priority and yield behavior; bare Lock is insufficient.
        self.assertIn("_LEAD_LOCK = _LeadSlot()", APP)
        self.assertIn("def _lead_slot(status):", APP)
        self.assertIn("class _LeadBusy", APP)
        self.assertIn("_LEAD_LOCK.acquire(timeout=None", APP)
        self.assertIn("_LEAD_LOCK.release()", APP)
        self.assertIn("ollama_client.YIELD_HOOK = _yield_point", APP)   # the price pause, then the hand-over (area C step 3)

    def test_helper_wired_at_all_three_explicit_paths(self):
        # Validates dispatch paths: force, on_unavailable fallback, warm (resident), escalation (Claude), and post-restart.
        self.assertIn("def _dispatch_to_lead(", APP)
        self.assertEqual(APP.count("_dispatch_to_lead("), 7)  # 1 def + 6 call sites
        self.assertIn("ensure=False", APP)            # warm path skips the wake
        self.assertIn("on_unavailable=lambda:", APP)  # escalation path offers Claude

    def test_temp_brain_path_serialized(self):
        # Cascade fallback to Mothership (both 8B down) serializes the 27B run too; 8B brains
        # use nullcontext so they never serialize.
        self.assertIn("brain_slot = _lead_slot(status) if b_host == config.HEAVY_HOST", APP)
        self.assertIn("contextlib.nullcontext()", APP)

    def test_busy_returns_friendly_error(self):
        self.assertIn("_LEAD_BUSY_RESULT", APP)
        self.assertIn("except _LeadBusy:", APP)


class _ConcurrencyProbe:
    """Records the maximum number of threads inside the critical section at once."""

    def __init__(self, dwell=0.2):
        self._lock = threading.Lock()
        self._dwell = dwell
        self.current = 0
        self.max = 0

    def __call__(self, *args, **kwargs):
        with self._lock:
            self.current += 1
            self.max = max(self.max, self.current)
        time.sleep(self._dwell)
        with self._lock:
            self.current -= 1
        return {"route": "heavyweight", "answer": "ok"}


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env (heavy deps absent)")
class SerializationBehaviorTests(unittest.TestCase):
    def setUp(self):
        self._orig_ensure = _app._ensure_lead
        self._orig_dispatch = _app._dispatch_lead
        _app._ensure_lead = lambda *_a, **_k: True
        # Tests slot serialization; mock `render_busy` to False to avoid flakiness from live hardware state.
        import comfy_client as _cc
        self._orig_busy = _cc.render_busy
        _cc.render_busy = lambda: False

    def tearDown(self):
        import comfy_client as _cc
        _cc.render_busy = self._orig_busy
        _app._ensure_lead = self._orig_ensure
        _app._dispatch_lead = self._orig_dispatch
        # Make sure no test left the slot held.
        self.assertFalse(_app._LEAD_LOCK.locked(), "Mothership slot left locked")

    def _run_heavy(self, results):
        res = _app._dispatch_to_lead(
            None, "t", [{}], [], False, None, "reason", deliberate=False)
        results.append(res)

    def test_concurrent_heavy_requests_never_overlap(self):
        probe = _ConcurrencyProbe(dwell=0.25)
        _app._dispatch_lead = probe
        results = []
        threads = [threading.Thread(target=self._run_heavy, args=(results,)) for _ in range(4)]
        for thr in threads:
            thr.start()
        for thr in threads:
            thr.join(timeout=10)
        self.assertEqual(len(results), 4)
        self.assertEqual(probe.max, 1, "two heavy requests entered the 27B at once")

    def test_slot_released_on_exception(self):
        def boom(*a, **k):
            raise RuntimeError("kaboom")
        _app._dispatch_lead = boom
        with self.assertRaises(RuntimeError):
            _app._dispatch_to_lead(None, "t", [{}], [], False, None, "r", deliberate=False)
        self.assertFalse(_app._LEAD_LOCK.locked(), "slot not released after an exception")

    def test_a_render_busy_refusal_returns_friendly_error(self):
        import comfy_client
        from unittest import mock
        _app._dispatch_lead = lambda *a, **k: {"route": "heavyweight"}
        with mock.patch.object(comfy_client, "render_busy", return_value=True):
            with self.assertRaises(_app._LeadBusy):      # the chat path catches it into the friendly message
                _app._dispatch_to_lead(None, "t", [{}], [], False, None, "r", deliberate=False)
        self.assertFalse(_app._LEAD_LOCK.locked(), "a refusal never takes the slot")


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env (heavy deps absent)")
class QuickFirstTests(unittest.TestCase):
    """A quick request goes ahead of waiting jobs, and a job holding Mothership hands it over between model turns,
    then takes it back."""

    def setUp(self):
        self.slot = _app._LeadSlot()
        self.slot.POLL = 0.02
        self.order = []

    def _hold(self, name, is_job, started=None, dwell=0.15):
        def run():
            if self.slot.acquire(timeout=10, is_job=is_job):
                self.order.append(name)
                if started:
                    started.set()
                time.sleep(dwell)
                self.slot.release()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_a_quick_request_goes_ahead_of_a_waiting_job(self):
        held = threading.Event()
        holder = self._hold("first", True, held, dwell=0.3)
        held.wait(2)
        job = self._hold("job", True)
        time.sleep(0.05)
        quick = self._hold("quick", False)
        for t in (holder, job, quick):
            t.join(5)
        self.assertEqual(self.order, ["first", "quick", "job"])

    def test_a_job_holding_the_slot_yields_between_turns_and_takes_it_back(self):
        events = []
        got_back = threading.Event()

        def long_job():
            self.slot.acquire(is_job=True)
            events.append("job turn 1")
            for _ in range(200):                      # until the quick request is queued
                if self.slot._queue:
                    break
                time.sleep(0.01)
            self.assertTrue(self.slot.yield_to_quick())
            events.append("job turn 2")
            got_back.set()
            self.slot.release()
        j = threading.Thread(target=long_job, daemon=True)
        j.start()
        time.sleep(0.05)
        q = threading.Thread(target=lambda: (self.slot.acquire(is_job=False), events.append("quick answer"),
                                             self.slot.release()), daemon=True)
        q.start()
        j.join(5)
        q.join(5)
        self.assertEqual(events, ["job turn 1", "quick answer", "job turn 2"])

    # --- area C step 3 (design § 6 step 8c): the price pause at a job's hand-over point ---
    def _price(self, band, usd=1.0):
        return {"band": band, "usd_per_kwh": usd, "source": "test"}

    def test_a_job_pauses_at_an_extreme_price_and_resumes_when_it_drops(self):
        """Wait for the price band to drop (re-reading every PRICE_RECHECK_S), not a fixed clock."""
        from unittest import mock
        self.slot.PRICE_RECHECK_S = 0.02
        price = self._price("extreme")
        events, lines, got_back = [], [], threading.Event()

        def job():
            self.slot.acquire(is_job=True)
            events.append("turn 1")
            events.append(("paused", self.slot.pause_for_price(status=lines.append)))
            events.append("turn 2")
            got_back.set()
            self.slot.release()
        with mock.patch.object(_app, "_price_now", lambda: dict(price)):
            j = threading.Thread(target=job, daemon=True)
            j.start()
            for _ in range(200):
                if lines and not self.slot.locked():
                    break
                time.sleep(0.01)
            self.assertFalse(self.slot.locked(), "the paused job released the slot")
            self.assertIn("extreme price ($1.00/kWh", lines[0])
            self.assertTrue(self.slot.acquire(timeout=1, is_job=False), "a quick request runs while the job is paused")
            events.append("quick answer")
            self.slot.release()
            time.sleep(0.06)
            self.assertFalse(got_back.is_set(), "still extreme: still paused")
            price["band"], price["usd_per_kwh"] = "normal", 0.11
            j.join(5)
        self.assertEqual(events, ["turn 1", "quick answer", ("paused", True), "turn 2"])
        self.assertTrue(any("resuming" in l for l in lines), lines)

    def test_a_priority_users_job_never_pauses_and_nothing_pauses_below_extreme(self):
        from unittest import mock
        with mock.patch.object(_app, "_price_now", lambda: self._price("extreme")):
            self.slot.acquire(is_job=True, prio=True)
            self.assertFalse(self.slot.pause_for_price(), "Alex's override (D3): a priority user's job never pauses")
            self.slot.release()
        for band in ("peak", "normal"):
            with mock.patch.object(_app, "_price_now", lambda b=band: self._price(b, 0.3)):
                self.slot.acquire(is_job=True)
                self.assertFalse(self.slot.pause_for_price(), "%s pauses nothing - peak is slow and low, the weighing's job" % band)
                self.slot.release()
        with mock.patch.object(_app, "_price_now", lambda: self._price("extreme")):
            self.slot.acquire(is_job=False)
            self.assertFalse(self.slot.pause_for_price(), "a quick request is not a job; nothing to pause")
            self.slot.release()

    def test_an_unreadable_price_never_pauses(self):
        from unittest import mock
        with mock.patch("scheduler._price_of_the_hour", side_effect=RuntimeError("sensor down")):
            self.assertEqual(_app._price_now()["band"], "normal")
            self.slot.acquire(is_job=True)
            self.assertFalse(self.slot.pause_for_price())
            self.slot.release()

    def test_a_stop_lands_during_a_price_pause(self):
        from unittest import mock
        import cancellation
        self.slot.PRICE_RECHECK_S = 0.02
        events, lines = [], []

        def job():
            cancellation.begin("q-price")
            cancellation.bind("q-price")
            try:
                self.slot.acquire(is_job=True)
                self.slot.pause_for_price(status=lines.append)
                events.append("resumed")
            except cancellation.Cancelled:
                events.append("stopped")
            finally:
                cancellation.unbind()
                cancellation.end("q-price")
        with mock.patch.object(_app, "_price_now", lambda: self._price("extreme")):
            j = threading.Thread(target=job, daemon=True)
            j.start()
            for _ in range(200):
                if lines:
                    break
                time.sleep(0.01)
            cancellation.cancel("q-price")
            j.join(5)
        self.assertEqual(events, ["stopped"])
        self.assertFalse(self.slot.locked())
        self.assertEqual(self.slot._queue, [], "a stopped pause leaves nothing queued")

    def test_a_waiting_request_is_told_its_place_and_the_rule_and_told_again_when_it_moves(self):
        """#37 step 6: several users at once - fairness SHOWN, never a silent wait."""
        self.slot.acquire(is_job=True)                       # a long job holds Mothership
        lines = []
        job2 = threading.Thread(target=lambda: (self.slot.acquire(is_job=True, status=lines.append),
                                                self.slot.release()), daemon=True)
        job2.start()
        for _ in range(200):
            if lines:
                break
            time.sleep(0.01)
        # #34 design s5: Queuing for a busy slot must provide explicit status (position, rule, ETA) and reroute options, never a silent wait.
        self.assertTrue(lines[-1].startswith(
            "Mothership is busy - you're queued: 1 ahead of you (quick requests go ahead of long jobs, "
            "so quick requests may pass you; the current job has held it "), lines[-1])
        self.assertIn("To answer now instead, Stop and pick Model: 8B", lines[-1])
        quick_lines = []
        quick = threading.Thread(target=lambda: (self.slot.acquire(is_job=False, status=quick_lines.append),
                                                 self.slot.release()), daemon=True)
        quick.start()
        for _ in range(200):
            if quick_lines and any("2 ahead" in l for l in lines):
                break
            time.sleep(0.01)
        self.assertIn("1 ahead of you (quick requests go first", quick_lines[-1], "the quick request jumped the job")
        self.assertTrue(any("2 ahead" in l for l in lines), "the job was told it moved back")
        self.slot.release()
        quick.join(5)
        job2.join(5)

    def test_jobs_do_not_preempt_each_other(self):
        self.slot.acquire(is_job=True)
        waiter = self._hold("job2", True)
        for _ in range(100):
            if self.slot._queue:
                break
            time.sleep(0.01)
        self.assertFalse(self.slot.yield_to_quick(), "a waiting JOB is not a reason to hand over")
        self.slot.release()
        waiter.join(5)
        self.assertEqual(self.order, ["job2"])

    def test_a_stop_lands_while_queued(self):
        import cancellation
        self.slot.acquire(is_job=True)
        outcome = []

        def queued():
            cancellation.begin("q-stop")
            cancellation.bind("q-stop")
            try:
                self.slot.acquire(timeout=10, is_job=False)
                outcome.append("acquired")
            except cancellation.Cancelled:
                outcome.append("stopped")
            finally:
                cancellation.unbind()
                cancellation.end("q-stop")
        t = threading.Thread(target=queued, daemon=True)
        t.start()
        time.sleep(0.1)
        cancellation.cancel("q-stop")
        t.join(3)
        self.assertEqual(outcome, ["stopped"])
        self.assertEqual(self.slot._queue, [], "a stopped request leaves the queue")
        self.slot.release()

    def test_a_stop_removes_only_the_stopped_waiters_place(self):
        """Removal must be by identity, not value: two waiters of the same kind are equal dicts, so value-based matching would remove the wrong entry."""
        import cancellation
        self.slot.acquire(is_job=True)
        got = []
        a = threading.Thread(target=lambda: (self.slot.acquire(is_job=False), got.append("a"), self.slot.release()),
                             daemon=True)
        a.start()
        for _ in range(200):
            if len(self.slot._queue) == 1:
                break
            time.sleep(0.01)

        def b():
            cancellation.begin("b-stop")
            cancellation.bind("b-stop")
            try:
                self.slot.acquire(is_job=False)
            except cancellation.Cancelled:
                got.append("b stopped")
            finally:
                cancellation.unbind()
                cancellation.end("b-stop")
        tb = threading.Thread(target=b, daemon=True)
        tb.start()
        for _ in range(200):
            if len(self.slot._queue) == 2:
                break
            time.sleep(0.01)
        cancellation.cancel("b-stop")
        tb.join(3)
        self.slot.release()
        a.join(3)
        self.assertEqual(got, ["b stopped", "a"])
        self.assertEqual(self.slot._queue, [])

    # The rough ETA comes from lengths the slot has seen, never a guess; when it has not seen the kind it needs,
    # the waiter is told why there is no number.
    def _queued(self, holder_job, entry_job, ahead=()):
        self.slot._holder, self.slot._holder_job = -1, holder_job
        self.slot._holder_since = self.slot._turn_mark = 1000.0
        entry = {"job": entry_job}
        self.slot._queue = [{"job": j} for j in ahead] + [entry]
        return entry

    def test_no_estimate_until_the_slot_has_seen_a_hold(self):
        entry = self._queued(False, False)
        s, why = self.slot.eta(entry, now=1010.0)
        self.assertIsNone(s)
        self.assertIn("no estimate yet", why)
        self.assertIn("no estimate yet", self.slot.queue_line(entry, 1, now=1010.0))

    def test_the_eta_is_what_is_left_of_the_holder_plus_each_request_ahead(self):
        self.slot._seen[False] = [100.0, 120.0, 140.0]           # median 120
        entry = self._queued(False, False, ahead=(False,))
        s, _ = self.slot.eta(entry, now=1030.0)                  # 90 left of the holder + 120 for the one ahead
        self.assertEqual(s, 210.0)
        self.assertIn("your turn in about 4 minutes", self.slot.queue_line(entry, 2, now=1030.0))

    def test_a_job_holder_is_timed_to_its_next_hand_over_point(self):
        self.slot._seen[True] = [50.0]
        entry = self._queued(True, False)
        s, _ = self.slot.eta(entry, now=1020.0)
        self.assertEqual(s, 30.0)
        self.assertEqual(self.slot._eta_words(s, ""), "your turn in about a minute")
        self.assertEqual(self.slot._eta_words(0.0, ""), "your turn should come any moment")
        self.assertEqual(self.slot._eta_words(60.7, ""), "your turn in about a minute")
        self.assertEqual(self.slot._eta_words(61.0, ""), "your turn in about 2 minutes")

    def test_a_job_behind_a_job_gets_no_number(self):
        self.slot._seen = {False: [10.0], True: [10.0]}
        for holder_job, ahead in ((True, ()), (False, (True,))):
            s, why = self.slot.eta(self._queued(holder_job, True, ahead), now=1001.0)
            self.assertIsNone(s)
            self.assertIn("long job", why)

    def test_the_queue_line_is_told_again_as_the_holders_time_grows(self):
        """The queue line is re-shown at least every HELD_RESHOW_S of the holder's time so the status never appears stale, even if the ETA text remains static. Updates are suppressed otherwise unless the position or ETA changes."""
        from unittest import mock
        clock = [1000.0]
        lines = []
        entry = self._queued(False, False)
        with mock.patch.object(_app.time, "monotonic", lambda: clock[0]):
            def waiter():
                with self.slot._cond:                                 # _wait_turn runs under the slot's condition (acquire)
                    self.slot._wait_turn(entry, None, status=lines.append)
            t = threading.Thread(target=waiter, daemon=True)
            t.start()
            for _ in range(100):
                if lines:
                    break
                time.sleep(0.02)
            n1 = len(lines)
            time.sleep(0.1)                                           # several polls, nothing moved: told nothing new
            self.assertEqual(len(lines), n1)
            clock[0] += self.slot.HELD_RESHOW_S + 1
            for _ in range(150):
                if len(lines) > n1:
                    break
                time.sleep(0.02)
            with self.slot._cond:
                self.slot._holder = None
                self.slot._cond.notify_all()
            t.join(3)
        self.assertGreaterEqual(len(lines), 2, lines)
        self.assertIn("held it 0 s", lines[0])
        self.assertIn("held it %d s" % int(self.slot.HELD_RESHOW_S + 1), lines[n1])

    def test_holds_and_hand_over_points_are_seen(self):
        self.slot.acquire(is_job=False)
        self.slot.release()
        self.assertEqual(len(self.slot._seen[False]), 1)
        self.slot.acquire(is_job=True)
        self.assertFalse(self.slot.yield_to_quick())             # nobody waiting: no hand-over, but a point SEEN
        self.slot.release()
        self.assertEqual(len(self.slot._seen[True]), 2)

    def test_only_the_holder_can_release(self):
        self.slot.acquire(is_job=True)
        other = threading.Thread(target=self.slot.release)
        other.start()
        other.join(2)
        self.assertTrue(self.slot.locked(), "another thread's release must not free the holder's slot")
        self.slot.release()
        self.assertFalse(self.slot.locked())


if __name__ == "__main__":
    unittest.main()
