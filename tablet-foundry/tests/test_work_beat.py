"""Reports in-flight CPU ticks to distinguish 'active but stuck' from 'idle'. Ticks are NOT progress; the watchdog still alerts on no finished items and uses this only to identify the failure mode."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil


class ProbeFindsTheAttemptTheStopWouldKill(unittest.TestCase):

    def test_same_cwd_match_as_the_kill(self):
        cmd = tablet_fulfil.attempt_cpu_cmd("tfoundry_stage/x")
        self.assertIn('S="$HOME/tfoundry_stage/x"', cmd)
        self.assertIn('readlink $p/cwd', cmd, "matched on the working directory, never the command line")
        self.assertIn('S="$HOME/tfoundry_stage/x"', tablet_fulfil.kill_attempt_cmd("tfoundry_stage/x"))

    @unittest.skipUnless(os.path.isdir("/proc/self") and shutil.which("bash"), "needs Linux /proc and bash")
    def test_live_decoy_a_busy_child_is_counted_and_an_empty_stage_reads_zero(self):
        home = tempfile.mkdtemp()
        stage = os.path.join(home, "tfoundry_stage", "decoy")
        os.makedirs(stage)
        env = dict(os.environ, HOME=home)
        # the payload shape: a parent in the stage dir whose CHILD does the work (llama-cli runs with cwd=LLM_DIR)
        # bounded: the child spins for 5 s and exits by itself, so cleanup never has to kill anything
        child = "import os, time\nos.chdir('/')\nend = time.time() + 5\nwhile time.time() < end: pass\n"
        parent = subprocess.Popen([sys.executable, "-c",
                                   "import os, subprocess, sys\nos.chdir(%r)\nsubprocess.call([sys.executable, '-c', %r])\n"
                                   % (stage, child)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.5)
            out = subprocess.run(["bash", "-c", tablet_fulfil.attempt_cpu_cmd("tfoundry_stage/decoy")],
                                 capture_output=True, text=True, env=env, timeout=30).stdout.split()
            self.assertEqual(out[0], "cpu", out)
            self.assertGreaterEqual(int(out[3]), 2, "parent AND its child: %s" % out)
            self.assertGreater(int(out[1]), 0, "a spinning child has used CPU: %s" % out)
            empty = subprocess.run(["bash", "-c", tablet_fulfil.attempt_cpu_cmd("tfoundry_stage/nothing")],
                                   capture_output=True, text=True, env=env, timeout=30).stdout.split()
            self.assertEqual(empty, ["cpu", "0", "procs", "0"])
        finally:
            # Cleanup must not raise; the decoy process self-terminates within 5 s
            try:
                parent.wait(timeout=30)
            except subprocess.TimeoutExpired:
                parent.kill()


class RunOrderBeatsWhileRowsAreFrozen(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.release = threading.Event()
        self.ticks = iter(range(100, 10000, 100))
        self.readable = True
        for p in (mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp),
                  mock.patch.object(tablet_fulfil, "SPECULATIVE_MIN_S", 9999.0),
                  mock.patch.object(tablet_fulfil, "WORK_PROBE_S", 0.2),
                  mock.patch.object(tablet_fulfil, "_ssh", self._ssh),
                  mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")),
                  mock.patch.object(tablet_fulfil, "_scp_from", lambda *a, **k: (1, "no result"))):
            p.start()
            self.addCleanup(p.stop)

    def _ssh(self, host, cmd, timeout=None):
        if "procs" in cmd:                          # the work probe
            return (0, "cpu %d procs 2" % next(self.ticks)) if self.readable else (255, "ssh: timed out")
        if "killed" in cmd:
            self.release.set()
            return 0, "killed 1"
        if "python" in cmd and "items.txt" in cmd:  # one long item
            self.release.wait(10)
            return 137, "Killed"
        return 0, ""

    def _run(self, seconds):
        beats, stop = [], threading.Event()
        spec = {"kind": "map", "name": "beat", "batch": 1, "payload": "/x/map_echo.py", "items": ["a"]}
        t = threading.Thread(target=lambda: tablet_fulfil.run_order(
            spec, ["boromir"], stop_event=stop, on_work=beats.append), daemon=True)
        t.start()
        time.sleep(seconds)
        stop.set()
        t.join(10)
        self.assertFalse(t.is_alive())
        return beats

    def test_a_long_item_beats_rising_cpu(self):
        beats = self._run(1.5)
        self.assertGreaterEqual(len(beats), 2, beats)
        self.assertEqual(beats, sorted(beats))
        self.assertGreater(beats[-1], beats[0])

    def test_an_unreadable_attempt_never_beats(self):
        # "could not tell" must not become a number: no beat, so the watchdog keeps the last value it had
        self.readable = False
        self.assertEqual(self._run(1.0), [])


class WatchdogReadsTheTabletBeat(unittest.TestCase):

    def setUp(self):
        import tablet_watch as TW
        self.TW = TW
        d = tempfile.mkdtemp()
        self.cur = os.path.join(d, "CURRENT_ORDER.json")
        hb = os.path.join(d, "runner.heartbeat")
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time() + 3600, "phase": "fulfilment"}, f)
        self.alerts = []
        for p in (mock.patch.object(TW, "CURRENT", self.cur),
                  mock.patch.object(TW, "HEARTBEAT", hb),
                  mock.patch.object(TW, "STAND_DOWN", os.path.join(d, "absent")),
                  mock.patch.object(TW, "alert", side_effect=lambda k, m, **kw: self.alerts.append((k, m))),
                  mock.patch.object(TW, "clear")):
            p.start()
            self.addCleanup(p.stop)

    def _claim(self, done, work=None):
        with open(self.cur, "w", encoding="utf-8") as f:
            json.dump({"name": "w1", "phase": "fulfilment", "done": done, "total": 121, "work": work}, f)

    def test_a_slow_grinding_answer_still_alerts_and_says_it_is_grinding(self):
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        self._claim(120, 5000)
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self._claim(120, 9000)
        ok, detail = self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertIn("ALERT", detail)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("still using CPU", self.alerts[0][1])

    def test_cpu_that_stopped_too_reads_dead(self):
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        self._claim(120, 9000)
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        motion.since -= self.TW.NO_PROGRESS_S + 5
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertIn("looks dead", self.alerts[0][1])
        self.assertNotIn("not moving", self.alerts[0][1], "it may not narrate what it did not measure")

    def test_without_a_beat_it_says_cpu_was_not_measured(self):
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        self._claim(120)
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertIn("not measured", self.alerts[0][1])

    def _store(self, rows):
        import sqlite3
        d = tempfile.mkdtemp()
        path = os.path.join(d, "t.sqlite")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE samples (ts REAL, node TEXT, proc TEXT, proc_cpu_pct REAL)")
        c.executemany("INSERT INTO samples VALUES (?,?,?,?)", rows)
        c.commit()
        c.close()
        return path

    def test_tablets_are_sampled_fast_only_while_a_run_is_stated(self):
        import tolkien_telemetry as tt
        self.assertEqual(tt.tablet_period("fleet:bench-x"), tt.TABLET_S)
        self.assertEqual(tt.tablet_period(""), tt.TABLET_IDLE_S)
        self.assertGreater(tt.TABLET_IDLE_S, tt.TABLET_S)
        self.assertLess(tt.TABLET_IDLE_S, 20, "a fresh reading must still arrive inside the preflight's 20 s wait")

    def test_the_stage_cpu_beat_is_the_newest_moment_a_stage_used_real_cpu(self):
        path = self._store([(100.0, "frodo", "rpc-server", 720.0), (160.0, "sam", "rpc-server", 3.0),
                            (150.0, "rigby", "llama-bench", 390.0), (170.0, "frodo", None, None)])
        self.assertEqual(self.TW.busy_beat(path), 150.0, "idle and non-process rows do not move it")
        self.assertIsNone(self.TW.busy_beat(self._store([(1.0, "sam", "rpc-server", 1.0)])), "never busy: no beat")
        self.assertIsNone(self.TW.busy_beat(os.path.join(tempfile.mkdtemp(), "absent.sqlite")),
                          "unreadable store: could not tell, never 'still'")

    def test_a_bench_order_with_dead_stages_reads_dead_not_unmeasured(self):
        """A bench order alone does not constitute evidence of work; the verdict must rely on actual stage CPU from the telemetry store."""
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        path = self._store([(100.0, "frodo", "rpc-server", 720.0)])
        self._claim(3)
        with mock.patch.object(self.TW, "TELEMETRY_DB", path):
            self.TW.patrol(prog, self.TW.FleetReach(), motion)
            prog.since -= self.TW.NO_PROGRESS_S + 5
            motion.since -= self.TW.NO_PROGRESS_S + 5
            self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertIn("looks dead", self.alerts[0][1])
        self.assertNotIn("not measured", self.alerts[0][1])

    def test_a_finished_item_is_progress_whatever_the_cpu_did(self):
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        self._claim(119, 9000)
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self._claim(120, 9000)
        ok, detail = self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertIn("advancing", detail)
        self.assertEqual(self.alerts, [])


if __name__ == "__main__":
    unittest.main()
