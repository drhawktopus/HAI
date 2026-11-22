"""Pins for triage_verify.py -- the library VERIFY lane instrument.

Every pin is BEHAVIOURAL: live local instruments (a real listener, a real temp file, a real
absent scheduled task) or a recorded-argv wrapper -- never a source-text grep. The two
load-bearing properties:

  * READ-ONLY: during a full run the only subprocess argvs are schtasks/query and tasklist,
    and the only artifact created in the sandbox is the verdicts file itself.
  * NEVER-SILENT: an unknown claim type produces a named ERROR row; zero rows addressed to
    this box exits rc 2 (a zero-match join is a broken join, not a clean sweep).
"""
import http.server
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import triage_verify as V


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestPathFresh(unittest.TestCase):
    def test_existing_path_is_current_with_age_evidence(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            p = f.name
        try:
            v, ev = V.h_path_fresh({"path": p})
        finally:
            os.unlink(p)
        self.assertEqual(v, "CURRENT")
        self.assertIn("days", ev)

    def test_absent_path_is_stale_never_error(self):
        v, ev = V.h_path_fresh({"path": os.path.join(tempfile.gettempdir(),
                                                     "__verify_absent_%d__" % os.getpid())})
        self.assertEqual(v, "STALE")

    def test_malformed_params_cannot_tell(self):
        v, _ = V.h_path_fresh({})
        self.assertEqual(v, "CANNOT-TELL")


class TestPortOpen(unittest.TestCase):
    def test_live_listener_is_current(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            v, ev = V.h_port_open({"ip": "127.0.0.1", "port": port})
        finally:
            srv.close()
        self.assertEqual(v, "CURRENT")
        self.assertIn("ms", ev)

    def test_closed_port_is_unreachable_not_stale(self):
        v, ev = V.h_port_open({"ip": "127.0.0.1", "port": _free_port()})
        self.assertEqual(v, "UNREACHABLE",
                         "a closed port could be a down service OR a stale line - the "
                         "instrument must not pick; the human decides")


class TestHttpReach(unittest.TestCase):
    def test_live_endpoint_is_current(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            v, ev = V.h_http_reach(
                {"url": "http://127.0.0.1:%d/health" % srv.server_port})
        finally:
            srv.shutdown()
        self.assertEqual(v, "CURRENT")
        self.assertIn("HTTP 200", ev)

    def test_no_listener_is_unreachable(self):
        v, _ = V.h_http_reach({"url": "http://127.0.0.1:%d/x" % _free_port()})
        self.assertEqual(v, "UNREACHABLE")


class TestTaskExists(unittest.TestCase):
    def test_absent_task_is_stale_live(self):
        v, ev = V.h_task_exists({"task": "__verify_no_such_task_%d__" % os.getpid()})
        self.assertEqual(v, "STALE", "live schtasks query for a name that cannot exist "
                                     "must map to STALE, got %r (%s)" % (v, ev))

    def test_present_task_is_current(self):
        class R:
            returncode = 0
            stdout = b"TaskName  Next Run Time  Status\r\nMyTask  N/A  Ready\r\n"
            stderr = b""
        with mock.patch.object(V.subprocess, "run", lambda *a, **k: R()):
            v, ev = V.h_task_exists({"task": "MyTask"})
        self.assertEqual(v, "CURRENT")
        self.assertIn("Ready", ev)

    def test_query_failure_is_cannot_tell_never_definite(self):
        def boom(*a, **k):
            raise OSError("no schtasks")
        with mock.patch.object(V.subprocess, "run", boom):
            v, _ = V.h_task_exists({"task": "X"})
        self.assertEqual(v, "CANNOT-TELL")


class TestProcRunning(unittest.TestCase):
    def test_absent_image_is_stale(self):
        v, _ = V.h_proc_running({"image": "__no_such_image_%d__.exe" % os.getpid()})
        self.assertEqual(v, "STALE")

    def test_observer_is_excluded(self):
        # The querying process itself must be excluded from results;
        # a CSV containing only this PID should indicate no running process.
        class R:
            returncode = 0
            stdout = ('"python.exe","%d","Console","1","10,000 K"\r\n'
                      % os.getpid()).encode()
            stderr = b""
        with mock.patch.object(V.subprocess, "run", lambda *a, **k: R()):
            v, ev = V.h_proc_running({"image": "python.exe"})
        self.assertEqual(v, "STALE")
        self.assertIn("observer", ev)


class TestPathStrippedInterpreter(unittest.TestCase):
    def test_p20_status_reuse_is_real_under_safe_path_mode(self):
        # An embedded-python ._pth builds sys.path without the script dir; `python -P`
        # replicates that. The import here is fail-soft, so a plain smoke test cannot
        # catch the miss -- this pin asserts p20_status actually bound.
        import subprocess
        import sys as _sys
        here = os.path.dirname(os.path.abspath(__file__))
        code = ("import sys; sys.path.insert(0, %r); import triage_verify as V; "
                "raise SystemExit(0 if V.p20_status is not None else 3)" % here)
        r = subprocess.run([_sys.executable, "-P", "-c", code],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0,
                         "p20_status must import under a path-stripped interpreter: %s"
                         % (r.stderr or r.stdout))


class TestFullRun(unittest.TestCase):
    """End-to-end through main(): addressing, error rows, cache, read-only, exit codes."""

    def _claims(self, rows):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "claims.json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump({"claims": rows}, f)
        return d, p

    def test_full_run_is_read_only_and_never_silent(self):
        port = _free_port()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        live = srv.getsockname()[1]
        with tempfile.NamedTemporaryFile(delete=False) as f:
            realfile = f.name
        rows = [
            {"id": "t1", "type": "path_fresh", "target_box": "testbox",
             "params": {"path": realfile}},
            {"id": "t2", "type": "port_open", "target_box": "testbox",
             "params": {"ip": "127.0.0.1", "port": live}},
            {"id": "t2b", "type": "port_open", "target_box": "testbox",
             "params": {"ip": "127.0.0.1", "port": live}},           # dup -> cache
            {"id": "t3", "type": "task_exists", "target_box": "testbox",
             "params": {"task": "__verify_gone_%d__" % os.getpid()}},
            {"id": "t4", "type": "bogus_type", "target_box": "testbox", "params": {}},
            {"id": "t5", "type": "judgment", "target_box": None, "params": {}},
            {"id": "t6", "type": "port_open", "target_box": "otherbox",
             "params": {"ip": "127.0.0.1", "port": port}},
        ]
        d, claims = self._claims(rows)
        out = os.path.join(d, "verdicts.json")
        seen_argv = []
        real_run = subprocess.run

        def recording(argv, *a, **k):
            seen_argv.append(list(argv))
            return real_run(argv, *a, **k)

        try:
            with mock.patch.object(V.subprocess, "run", recording):
                rc = V.main(["triage_verify.py", claims, out, "TestBox",
                             "--items-file", "ignored.json", "jobname"])
        finally:
            srv.close()
            os.unlink(realfile)
        self.assertEqual(rc, 0)

        # READ-ONLY, behaviourally: every subprocess is a query, and the sandbox gained
        # exactly the deliverable (claims.json was ours)
        for argv in seen_argv:
            self.assertIn(os.path.basename(str(argv[0])).lower(),
                          ("schtasks", "tasklist"),
                          "non-allowlisted subprocess: %r" % argv)
            if "schtasks" in str(argv[0]).lower():
                self.assertEqual(argv[1], "/query")
        self.assertEqual(sorted(os.listdir(d)), ["claims.json", "verdicts.json"],
                         "the deliverable must be the ONLY artifact created")

        got = json.load(io.open(out, encoding="utf-8"))
        by_id = {v["id"]: v for v in got["verdicts"]}
        self.assertEqual(by_id["t1"]["verdict"], "CURRENT")
        self.assertEqual(by_id["t2"]["verdict"], "CURRENT")
        self.assertIn("cached", by_id["t2b"]["evidence"],
                      "identical probes must be answered once")
        self.assertEqual(by_id["t3"]["verdict"], "STALE")
        self.assertEqual(by_id["t4"]["verdict"], "ERROR")
        self.assertIn("bogus_type", by_id["t4"]["evidence"])
        self.assertNotIn("t5", by_id, "judgment rows are the human's, never probed")
        self.assertNotIn("t6", by_id, "other-box rows are not this box's to answer")
        self.assertEqual(got["rows_mine"], 5)
        self.assertEqual(got["rows_judgment"], 1)
        self.assertEqual(got["rows_other_box"], 1)

    def test_zero_addressed_rows_exits_2(self):
        d, claims = self._claims([{"id": "x", "type": "port_open",
                                   "target_box": "otherbox",
                                   "params": {"ip": "127.0.0.1", "port": 1}}])
        rc = V.main(["triage_verify.py", claims, os.path.join(d, "o.json"), "testbox"])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(d, "o.json")),
                         "a broken join must ship nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
