"""Idle wait polls for new work every QUICK_POLL_S rather than sleeping the full cycle to minimize latency."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_runner as TR


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class IdleWait(unittest.TestCase):
    def test_it_returns_the_moment_work_is_reported(self):
        clk = FakeClock()
        seen = []

        def collect():
            seen.append(clk.t)
            return len(seen) >= 2          # work appears after the second ask
        self.assertTrue(TR._idle_wait(30.0, collect, sleep=clk.sleep, clock=clk))
        self.assertEqual(len(seen), 2)
        self.assertAlmostEqual(clk.t - 1000.0, 2 * TR.QUICK_POLL_S)

    def test_it_sleeps_the_whole_poll_when_nothing_comes_and_asks_at_the_quick_cadence(self):
        clk = FakeClock()
        asks = []
        self.assertFalse(TR._idle_wait(5.0, lambda: asks.append(clk.t) and False, sleep=clk.sleep, clock=clk))
        self.assertAlmostEqual(clk.t - 1000.0, 5.0)
        self.assertEqual(len(asks), int(round(5.0 / TR.QUICK_POLL_S)))

    def test_a_failing_ask_does_not_end_the_wait(self):
        clk = FakeClock()

        def collect():
            raise OSError("desk down")
        self.assertFalse(TR._idle_wait(3.0, collect, sleep=clk.sleep, clock=clk))
        self.assertAlmostEqual(clk.t - 1000.0, 3.0)

    def test_the_quick_cadence_is_the_windows_directory_listing_cadence(self):
        # QUICK_POLL_S is the Windows directory listing cadence; do not tune independently
        self.assertEqual(TR.QUICK_POLL_S, 1.0)


class WorkWaiting(unittest.TestCase):
    def test_a_queued_order_of_the_lane_not_held_by_a_lane_busy_wait_is_work(self):
        with mock.patch.object(TR, "queued", return_value=["/q/r1.tjob.json", "/q/f1.tjob.json"]), \
                mock.patch.object(TR, "lane_of", side_effect=lambda p: "rigby" if "/r" in p else "fleet"):
            self.assertTrue(TR._work_waiting("rigby", {}, now=100.0))
            self.assertTrue(TR._work_waiting("fleet", {}, now=100.0))
            self.assertFalse(TR._work_waiting("rigby", {"/q/r1.tjob.json": 200.0}, now=100.0), "waiting out a LaneBusy")
            self.assertTrue(TR._work_waiting("rigby", {"/q/r1.tjob.json": 90.0}, now=100.0), "its wait is up")
        with mock.patch.object(TR, "queued", return_value=["/q/f1.tjob.json"]), \
                mock.patch.object(TR, "lane_of", return_value="fleet"):
            self.assertFalse(TR._work_waiting("rigby", {}, now=100.0), "the other lane's order is not this lane's work")

    def test_an_unreadable_queue_is_no_work_never_a_raise(self):
        with mock.patch.object(TR, "queued", side_effect=OSError("share gone")):
            self.assertFalse(TR._work_waiting("rigby", {}))


class TheFleetLanesQuickAskReadsTheInbox(unittest.TestCase):
    """Quick ask drains the inbox immediately to avoid the latency of waiting for the full pass to read it."""

    def ask(self, inbox=0, desk=0):
        TR.NODES_FREED.clear()
        with mock.patch.object(TR, "push_capacity_if_changed", return_value=False), \
                mock.patch.object(TR, "collect_fleet_orders", return_value=desk), \
                mock.patch.object(TR, "drain_inbox", return_value=inbox) as drain, \
                mock.patch.object(TR, "rebind_to_free_worker", return_value=0), \
                mock.patch.object(TR, "_fleet_work_waiting", return_value=False):
            return TR._fleet_quick_check({}), drain.call_count

    def test_an_order_in_the_inbox_starts_a_pass_at_once(self):
        self.assertEqual(self.ask(inbox=1), (True, 1))

    def test_an_empty_inbox_and_no_desk_work_keeps_waiting_and_the_inbox_was_still_read(self):
        self.assertEqual(self.ask(), (False, 1))

    def test_desk_work_is_seen_without_waiting_on_the_inbox(self):
        self.assertTrue(self.ask(desk=1)[0])


class BothLanesUseIt(unittest.TestCase):
    """The two idle sleeps are the only places a lane waits between passes; both go through _idle_wait."""

    def test_no_bare_poll_sleep_is_left_in_either_lane(self):
        import inspect
        for fn in (TR.run_queue, TR.rigby_lane):
            src = "\n".join(l.split("#")[0] for l in inspect.getsource(fn).splitlines())
            self.assertNotIn("time.sleep(min(poll", src, fn.__name__)
            self.assertIn("_idle_wait(", src, fn.__name__)


if __name__ == "__main__":
    unittest.main()
