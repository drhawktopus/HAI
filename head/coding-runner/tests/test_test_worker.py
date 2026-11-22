"""A job's tests on another box (test_worker.py): the same limits and result as a local run, what they write comes back, and a box that
cannot take it costs the job nothing. A real HTTP server and real test processes; only the box is this one."""
import base64
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import test_worker as W
from runner import CodingRunner

TOKEN = "t0ken"
WRITES = ("import os,pathlib;"
          "print('RAN IN', os.path.basename(os.getcwd()));"
          "print('ENV FILE THERE', os.path.exists('../.env') or os.path.exists('.env'));"
          "pathlib.Path('made.stl').write_text('solid');"
          "pathlib.Path('gone.txt').unlink(missing_ok=True)")
GROWS = "x=[]\nwhile True:\n    x.append(bytearray(10**6))\n"


class _Box(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.workspace = base / "workspace"
        (self.workspace / "job").mkdir(parents=True)
        (self.workspace / ".env").write_text("SECRET=nope\n", encoding="utf-8")
        (self.workspace / "job" / "gone.txt").write_text("x", encoding="utf-8")
        for args in (("init",), ("config", "user.email", "r@example.invalid"), ("config", "user.name", "R"), ("add", "."), ("commit", "-m", "fixture")):
            subprocess.run(["git", *args], cwd=self.workspace, check=True, capture_output=True)
        registry = {"workspaces": [{"id": "fixture", "path": str(self.workspace), "description": "t", "read": True, "write": True, "git": True,
                                    "protected_paths": [".env"], "ignored_paths": [".git"],
                                    "shared": True, "commands": {"writes": [sys.executable, "-c", WRITES], "grows": [sys.executable, "-c", GROWS]}}]}
        (base / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(base / "registry.json", base / "state")
        self.server = W.make_server(0, TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.runner.test_workers = {"box": "http://127.0.0.1:%d" % self.server.server_address[1]}
        self.runner.test_worker_token = TOKEN
        # The box is a quiet one unless a test says otherwise: unstubbed, these read the REAL host's load and memory, and on a
        # busy host every run goes "here instead" and the tests fail. A test that
        # needs a busy or short box patches it inside, as SeveralAtOnce always did.
        for p in (mock.patch("os.getloadavg", lambda: (0.0, 0.0, 0.0), create=True),   # the thing the busy-box test overrides
                  mock.patch.object(W, "_mem_available_gb", lambda: 64.0)):
            p.start()
            self.addCleanup(p.stop)


class ARunOnTheBox(_Box):
    def test_it_runs_there_and_what_it_wrote_comes_back(self):
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertEqual(out["ran_on"], "box")
        self.assertEqual(out["returncode"], 0, out)
        self.assertIn("RAN IN job", out["stdout"])
        self.assertEqual((self.workspace / "job" / "made.stl").read_text(), "solid", "a file the test wrote lands in the job's folder")
        self.assertFalse((self.workspace / "job" / "gone.txt").exists(), "a file the test removed goes too")

    def test_a_protected_file_is_never_sent(self):
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertIn("ENV FILE THERE False", out["stdout"])
        self.assertTrue((self.workspace / ".env").exists(), "and it stays where it is")

    def test_a_runaway_stops_at_the_limit_with_the_same_words_as_here(self):
        self.runner.test_memory_gb = 0.2
        self.runner.test_run_timeout_seconds = 60
        out = self.runner.test_run("fixture", "grows", "job", "box")
        self.assertEqual(out["ran_on"], "box")
        self.assertIn("memory", out.get("ended_because", ""))
        self.assertFalse(out["timed_out"])
        self.assertIn("File", out["stderr"], "the stack dump comes back")


class ItRunsHereInstead(_Box):
    def test_nobody_answering_runs_it_here_and_says_so(self):
        self.runner.test_workers = {"box": "http://127.0.0.1:9"}
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertEqual(out["returncode"], 0)
        self.assertNotIn("ran_on", out)
        self.assertIn("did not answer", out["ran_here_because"])
        self.assertTrue((self.workspace / "job" / "made.stl").exists())

    def test_a_box_working_hard_runs_it_here(self):
        with mock.patch("os.getloadavg", lambda: (99.0, 0, 0)):
            out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertIn("working hard", out["ran_here_because"])

    def test_a_box_short_of_memory_runs_it_here(self):
        with mock.patch.object(W, "_mem_available_gb", lambda: 1.0):
            out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertIn("GB free", out["ran_here_because"])

    def test_only_a_shared_workspace_leaves_the_box(self):
        self.runner.workspaces["fixture"].__dict__["shared"] = False
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertNotIn("ran_on", out)
        self.assertNotIn("ran_here_because", out)

    def test_a_box_nobody_named_runs_it_here(self):
        out = self.runner.test_run("fixture", "writes", "job", "nosuchbox")
        self.assertNotIn("ran_on", out)

    def test_a_wrong_token_is_refused(self):
        self.runner.test_worker_token = "wrong"
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertIn("did not answer", out["ran_here_because"], "a 401 is not a run")
        self.assertNotIn("ran_on", out)

    def test_with_no_box_set_nothing_changes(self):
        self.runner.test_workers = {}
        out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertNotIn("ran_on", out)
        self.assertNotIn("ran_here_because", out)


class SeveralAtOnce(_Box):
    """Admission by memory: as many as fit at their whole limit. Each test states its memory, because the box running the suite has its own."""

    def tearDown(self):
        W._NOW.update(runs=0, held_gb=0.0)

    def test_two_fit_and_a_third_does_not(self):
        with mock.patch.object(W, "_mem_available_gb", lambda: 18.0), mock.patch("os.getloadavg", lambda: (0.0, 0, 0)):
            self.assertIsNone(W.admit(8))
            self.assertIsNone(W.admit(8))
            self.assertIn("GB free", W.admit(8))
            W.release(8)
            self.assertIsNone(W.admit(8), "a finished run gives its room back")

    def test_a_run_goes_ahead_beside_another_when_it_fits(self):
        with mock.patch.object(W, "_mem_available_gb", lambda: 64.0), mock.patch("os.getloadavg", lambda: (0.0, 0, 0)):
            self.assertIsNone(W.admit(8))
            out = self.runner.test_run("fixture", "writes", "job", "box")
        self.assertEqual(out["ran_on"], "box", out)

    def test_its_room_comes_back_after_a_run(self):
        with mock.patch.object(W, "_mem_available_gb", lambda: 64.0), mock.patch("os.getloadavg", lambda: (0.0, 0, 0)):
            self.runner.test_run("fixture", "writes", "job", "box")
        self.assertEqual(W._NOW, {"runs": 0, "held_gb": 0.0})

    def test_a_box_that_cannot_say_runs_one_at_a_time(self):
        with mock.patch.object(W, "_mem_available_gb", lambda: None), mock.patch("os.getloadavg", lambda: (0.0, 0, 0)):
            self.assertIsNone(W.admit(8))
            self.assertIn("cannot say", W.admit(8))


class TheBundleStaysInItsFolder(unittest.TestCase):
    def test_nothing_lands_outside(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("ok.txt", "../escaped.txt"):
                ti = tarfile.TarInfo(name)
                ti.size = 1
                tar.addfile(ti, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "in"
            dest.mkdir()
            W.unpack(buf.getvalue(), dest)
            self.assertTrue((dest / "ok.txt").exists())
            self.assertFalse((Path(d) / "escaped.txt").exists())

    def test_a_test_folder_outside_the_bundle_is_refused(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz"):
            pass
        req = {"bundle_b64": base64.b64encode(buf.getvalue()).decode(), "cwd_rel": "../..", "argv": ["x"], "timeout": 1}
        with self.assertRaises(ValueError):
            W.run_request(req, executor=object())


if __name__ == "__main__":
    unittest.main()
