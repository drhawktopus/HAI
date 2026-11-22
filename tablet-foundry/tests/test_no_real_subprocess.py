"""Guard test suite against spawning real subprocesses.

Ensures no unit test reaches `subprocess.Popen`. Uses a sentinel that records
calls instead of executing. If the sentinel is called, it means a test failed
to patch Popen properly and reached the real implementation.

Also asserts that tests actually ran (avoiding false negatives from empty suites).
"""
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
# Ensure tests/ is importable from any cwd, preventing `loadTestsFromName` from
# silently returning empty results when invoked outside the tests directory.
sys.path.insert(0, _HERE)

# Modules whose code paths can construct a process. Adding a new one here is deliberate.
PROCESS_TOUCHING = ["test_tolkien_burst", "test_tolkien_dispatch"]


class NoRealSubprocessInUnitTests(unittest.TestCase):
    def test_no_unit_test_spawns_a_real_process(self):
        spawned = []

        def sentinel(*a, **kw):
            spawned.append(a[0] if a else kw.get("args"))
            inst = mock.MagicMock()
            inst.communicate.return_value = (b"", b"")
            inst.returncode = 0
            inst.stdout = io.BytesIO(b"")
            inst.args = a[0] if a else None
            return inst

        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for name in PROCESS_TOUCHING:
            suite.addTests(loader.loadTestsFromName(name))

        ran = suite.countTestCases()
        self.assertGreater(ran, 0, "the guard executed NO tests - it is measuring nothing, which "
                                   "looks identical to a clean result")

        sink = io.open(os.devnull, "w", encoding="utf-8", errors="replace")
        try:
            with mock.patch.object(subprocess, "Popen", side_effect=sentinel):
                res = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
        finally:
            sink.close()

        self.assertEqual(
            spawned, [],
            "%d unit test(s) reached the real subprocess.Popen: %s\n"
            "Patch it with mock.patch.object(subprocess, 'Popen', autospec=True) — see "
            "install_fake_popen() in test_tolkien_burst.py." % (len(spawned), spawned))

        self.assertGreater(res.testsRun, 0, "the guarded run executed nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
