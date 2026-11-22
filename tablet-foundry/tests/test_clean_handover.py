"""Clean handover on stop: release lock only if holder, never while old taker still beats (prevent dual control).
Install SIGTERM handler as Python is PID 1 (ignores SIGTERM by default)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as TF  # noqa: E402


class _Lock(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.logged = []
        for x in (mock.patch.object(TF, "STATE_DIR", self.d),
                  mock.patch.object(TF, "RUNNER_LOCK", os.path.join(self.d, "runner.lock")),
                  mock.patch.object(TF, "HEARTBEAT", os.path.join(self.d, "runner.heartbeat")),
                  mock.patch.object(TF, "INSTANCE", "rigby-1-NEW"),
                  mock.patch.object(TF, "log", side_effect=lambda m: self.logged.append(m))):
            x.start()
            self.addCleanup(x.stop)

    def lock(self, **rec):
        with open(TF.RUNNER_LOCK, "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def beat_at(self, ts):
        with open(TF.HEARTBEAT, "w", encoding="utf-8") as f:
            json.dump({"ts": ts}, f)

    def held(self):
        return json.load(open(TF.RUNNER_LOCK, encoding="utf-8"))


class Takeover(_Lock):
    def test_a_released_lock_is_taken_at_once(self):
        now = time.time()
        self.lock(instance="rigby-1-OLD", ts=now - 5, released=now - 0.2)
        self.beat_at(now - 0.5)                                 # its last beat came BEFORE the release
        self.assertTrue(TF.acquire_singleton())
        self.assertEqual(self.held()["instance"], "rigby-1-NEW")
        self.assertTrue(any("released the lock" in m for m in self.logged), self.logged)

    def test_an_unreleased_live_lock_still_waits(self):
        now = time.time()
        self.lock(instance="rigby-1-OLD", ts=now - 5)
        self.beat_at(now - 1)
        self.assertFalse(TF.acquire_singleton())
        self.assertEqual(self.held()["instance"], "rigby-1-OLD")

    def test_a_beat_after_the_release_means_it_is_still_running_never_two_takers(self):
        now = time.time()
        self.lock(instance="rigby-1-OLD", ts=now - 30, released=now - 20)
        self.beat_at(now - 1)                                   # it kept beating after it said it released
        self.assertFalse(TF.acquire_singleton())
        self.assertEqual(self.held()["instance"], "rigby-1-OLD")

    def test_a_quiet_unreleased_lock_is_still_taken_after_the_old_rule(self):
        now = time.time()
        self.lock(instance="rigby-1-OLD", ts=now - 200)
        self.beat_at(now - 120)
        self.assertTrue(TF.acquire_singleton(stale_after=90.0))
        self.assertTrue(any("went quiet" in m for m in self.logged), self.logged)


class Release(_Lock):
    def test_only_the_holder_releases(self):
        self.lock(instance="rigby-1-NEW", ts=time.time())
        self.assertTrue(TF.release_singleton())
        self.assertIn("released", self.held())
        self.lock(instance="rigby-1-SOMEONE", ts=time.time())
        self.assertFalse(TF.release_singleton())
        self.assertNotIn("released", self.held(), "another instance's lock is never touched")

    def test_release_never_raises(self):
        self.assertFalse(TF.release_singleton())                 # no lock at all
        with open(TF.RUNNER_LOCK, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertFalse(TF.release_singleton())

    def test_a_full_handover_from_one_instance_to_the_next(self):
        with mock.patch.object(TF, "INSTANCE", "rigby-1-A"):
            self.assertTrue(TF.acquire_singleton())
            self.beat_at(time.time() - 0.5)
            self.assertTrue(TF.release_singleton())
        with mock.patch.object(TF, "INSTANCE", "rigby-1-B"):
            t0 = time.time()
            self.assertTrue(TF.acquire_singleton())
            self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(self.held()["instance"], "rigby-1-B")


class StopPath(unittest.TestCase):
    def test_a_stop_runs_its_cleanup_then_exits_zero(self):
        seen = []
        with mock.patch("signal.signal") as sig:
            handler = TF.install_stop(on_stop=lambda: seen.append("released"), exit_fn=lambda c: seen.append(c))
        import signal
        self.assertEqual(sig.call_args[0][0], signal.SIGTERM)
        handler(signal.SIGTERM, None)
        self.assertEqual(seen, ["released", 0])

    def test_a_failing_cleanup_still_exits(self):
        seen = []

        def boom():
            raise RuntimeError("x")
        with mock.patch("signal.signal"):
            handler = TF.install_stop(on_stop=boom, exit_fn=lambda c: seen.append(c))
        handler(15, None)
        self.assertEqual(seen, [0])


class EveryContainerInstallsItsStop(unittest.TestCase):
    """PID 1 ignores SIGTERM without a handler: each container's entry point installs one before its work starts."""

    class Sentinel(Exception):
        pass

    def _first(self):
        return mock.patch.object(TF, "install_stop", side_effect=self.Sentinel)

    def test_the_order_taker(self):
        import tablet_runner
        with self._first() as inst, mock.patch.object(tablet_runner.os, "makedirs"):
            with self.assertRaises(self.Sentinel):
                tablet_runner.run_queue()
        self.assertIs(inst.call_args.kwargs.get("on_stop"), TF.release_singleton)

    def test_the_watchdog(self):
        import tablet_watch
        with self._first(), self.assertRaises(self.Sentinel):
            tablet_watch.main()

    def test_the_telemetry_logger(self):
        import tolkien_telemetry
        with self._first(), mock.patch.object(tolkien_telemetry, "run_logger") as run:
            with self.assertRaises(self.Sentinel):
                tolkien_telemetry.main([])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
