r"""Tests for foundry_lock.\n\nThese assert the PROPERTY (does the lock correctly identify its holder?), never the implementation details.\nA future rewrite that stores a token in the lock file instead of comparing mtimes stays green iff it\nstill refuses to mistake a recycled PID for the holder.\n\ntest_recycled_pid_is_not_the_holder is the critical safety invariant: the old code passes every other test.\n"""
import os
import tempfile
import unittest

import foundry_lock


def _lock(pid, mtime=None):
    fd, path = tempfile.mkstemp(suffix=".lock")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(pid))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestHolderIsLive(unittest.TestCase):
    def tearDown(self):
        for p in getattr(self, "_paths", []):
            try:
                os.remove(p)
            except OSError:
                pass

    def track(self, path):
        self._paths = getattr(self, "_paths", []) + [path]
        return path

    # --- THE OUTAGE ------------------------------------------------------------------------
    def test_recycled_pid_is_not_the_holder(self):
        written = 1000.0
        path = self.track(_lock(16212, mtime=written))
        live, reason = foundry_lock.holder_is_live(path, my_pid=999,
                                                   _start_epoch=written + 1218.0)
        self.assertFalse(live, "a pid that started AFTER the lock was written is not the holder")
        self.assertIn("recycled", reason)

    def test_the_real_holder_is_still_respected(self):
        """The bug fix must not turn every lock into a free-for-all: a genuine holder started
        before it wrote its lock, and must still block a second instance."""
        written = 1000.0
        path = self.track(_lock(4242, mtime=written))
        live, reason = foundry_lock.holder_is_live(path, my_pid=999, _start_epoch=written - 0.2)
        self.assertTrue(live, "the process that wrote the lock must still hold it")
        self.assertIn("4242", reason)

    def test_a_dead_pid_is_reclaimed(self):
        """The behaviour that already worked (four times in the journals) must survive."""
        path = self.track(_lock(12536, mtime=1000.0))
        live, reason = foundry_lock.holder_is_live(path, my_pid=999, _start_epoch=None,
                                                   _mtime=1000.0)
        # _start_epoch=None means "ask the OS"; pid 12536 does not exist here.
        self.assertFalse(live)
        self.assertIn("not running", reason)

    # --- could-not-tell is never rendered as a definite state -------------------------------
    def test_unreadable_lock_reclaims_rather_than_stands_down(self):
        live, reason = foundry_lock.holder_is_live(
            os.path.join(tempfile.gettempdir(), "definitely-not-here-9f3a.lock"), my_pid=999)
        self.assertFalse(live, "cannot tell must reclaim, not stand down")
        self.assertIn("unreadable", reason)

    def test_garbage_lock_contents_reclaim(self):
        fd, path = tempfile.mkstemp(suffix=".lock")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("not-a-pid")
        self.track(path)
        live, reason = foundry_lock.holder_is_live(path, my_pid=999)
        self.assertFalse(live)
        self.assertIn("no pid", reason)

    def test_lock_naming_ourselves_is_not_a_blocker(self):
        path = self.track(_lock(4242, mtime=1000.0))
        live, reason = foundry_lock.holder_is_live(path, my_pid=4242)
        self.assertFalse(live)
        self.assertIn("this very process", reason)

    # --- the reason string is an OBSERVATION, not an assertion ------------------------------
    def test_every_reason_reports_what_was_seen(self):
        """A guard may not narrate a state it did not measure. Each reason must name the concrete
        thing observed (a pid, a file condition), never a bare verdict."""
        written = 1000.0
        cases = [
            (self.track(_lock(16212, mtime=written)), 999, written + 1218.0),
            (self.track(_lock(4242, mtime=written)), 999, written - 0.2),
        ]
        for path, mine, start in cases:
            _, reason = foundry_lock.holder_is_live(path, my_pid=mine, _start_epoch=start)
            self.assertTrue(any(ch.isdigit() for ch in reason),
                            "reason %r states no observation" % reason)


class TestProcStartEpoch(unittest.TestCase):
    def test_our_own_process_has_a_start_time(self):
        """POSITIVE CONTROL. A None from this function must mean 'no such process', not 'the
        query is broken' -- so prove it answers for a process we know exists before trusting any
        negative it returns."""
        got = foundry_lock.proc_start_epoch(os.getpid())
        self.assertIsNotNone(got, "the instrument cannot see even our own process")
        self.assertGreater(got, 1_600_000_000, "implausible epoch %r" % got)

    def test_absent_pid_returns_none(self):
        self.assertIsNone(foundry_lock.proc_start_epoch(999_999_999))

    def test_nonsense_pid_returns_none_without_raising(self):
        self.assertIsNone(foundry_lock.proc_start_epoch(None))
        self.assertIsNone(foundry_lock.proc_start_epoch("abc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
