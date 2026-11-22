"""The client timeout for coding_test must exceed the runner's execution limit (default 300s); otherwise, the client aborts before the runner completes and no output is returned. The path argument must be passed from the tool schema through the dispatcher to the client to isolate test execution to the specific job directory. Other runner calls retain the standard short timeout."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coding_client  # noqa: E402
import config  # noqa: E402
import tools  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class TestCallsWaitForTheRunner(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reply = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False, "timeout_seconds": 300}
        p = mock.patch.object(coding_client.httpx, "request",
                              lambda method, url, **kw: self.calls.append((url, kw)) or _Resp(self.reply))
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.dict(coding_client._RUNNER_TEST_S, {"s": None})
        q.start()
        self.addCleanup(q.stop)

    def test_a_test_call_outwaits_the_runners_own_limit(self):
        coding_client.test("hai-scratch", "unit")
        url, kw = self.calls[-1]
        self.assertTrue(url.endswith("/tests/run"))
        self.assertGreater(kw["timeout"], 300, "the runner kills at 300 s by default; the caller must wait past that")
        self.assertGreater(kw["timeout"], config.CODING_RUNNER_TIMEOUT)

    def test_the_runner_reported_limit_moves_the_wait(self):
        self.reply = dict(self.reply, timeout_seconds=900)
        coding_client.test("hai-scratch", "unit")
        coding_client.test("hai-scratch", "unit")
        self.assertGreaterEqual(self.calls[-1][1]["timeout"], 900 + config.CODING_TEST_MARGIN)

    def test_other_calls_keep_the_short_wait(self):
        coding_client.read("hai-scratch", "x.py")
        self.assertEqual(self.calls[-1][1]["timeout"], config.CODING_RUNNER_TIMEOUT)

    def test_path_reaches_the_runner_and_is_absent_when_not_given(self):
        coding_client.test("hai-scratch", "unit", None, "ltt-jarvis/cookie-auto")
        self.assertEqual(self.calls[-1][1]["json"]["path"], "ltt-jarvis/cookie-auto")
        coding_client.test("hai-scratch", "unit")
        self.assertNotIn("path", self.calls[-1][1]["json"])


class TheToolOffersAndPassesPath(unittest.TestCase):
    def test_schema_and_dispatch(self):
        self.assertIn("path", tools._CODING_TEST["function"]["parameters"]["properties"])
        seen = {}
        with mock.patch.object(tools.coding_client, "test",
                               lambda *a, **k: seen.setdefault("args", a) or {"returncode": 0}), \
                mock.patch("coding_test_box.free_box", lambda: None):
            tools.run_tool("coding_test", {"workspace": "hai-scratch", "profile": "unit",
                                           "path": "ltt-jarvis/cookie-auto"}, "")
        self.assertEqual(seen["args"][-1], "ltt-jarvis/cookie-auto")


class TheToolSaysItIsTheOnlyWayToRunCode(unittest.TestCase):
    """`coding_test` is the only HAI tool that executes code, so its description is the sole source of truth for the model.
    The description must explicitly state this uniqueness and define file retention behavior."""

    def test_the_description_names_the_route_to_a_generated_file(self):
        d = tools._CODING_TEST["function"]["description"]
        self.assertIn("only tool that executes code", d)
        self.assertIn("A file a test writes inside the job's folder stays", d)


if __name__ == "__main__":
    unittest.main()
