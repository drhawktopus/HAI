"""A plain POST /query is a ledgered run, worked the same way as a streamed one, with its result returned."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class PlainQueryIsARun(unittest.TestCase):
    def test_accepted_then_worked_by_the_stream_worker_and_the_result_returned(self):
        q = app.Q(user="test", text="hello")
        seen = {}
        def accept(qq, cid):
            seen["accept"] = (qq, cid)
        def worker(qq, cid):
            seen["work"] = (qq, cid)
            return {"route": "ok", "answer": "fine"}
        with mock.patch.object(app, "_enforce_identity"), mock.patch.object(app, "_admit_background"), \
             mock.patch.object(app, "_accept_turn", side_effect=accept), \
             mock.patch.object(app, "_stream_worker", side_effect=worker):
            out = app.query(q, request=None)
        self.assertEqual(out, {"route": "ok", "answer": "fine"})
        self.assertEqual(seen["accept"][0], q)
        self.assertEqual(seen["accept"][1], q.cancel_id, "the run id the ledger and the feed are keyed on")
        self.assertEqual(seen["work"], seen["accept"], "accepted first, then worked, as one run")
        self.assertTrue(q.cancel_id)

    def test_a_worker_with_no_result_still_answers_the_caller(self):
        q = app.Q(user="test", text="hello")
        with mock.patch.object(app, "_enforce_identity"), mock.patch.object(app, "_admit_background"), \
             mock.patch.object(app, "_accept_turn"), mock.patch.object(app, "_stream_worker", return_value=None):
            out = app.query(q, request=None)
        self.assertEqual(out.get("route"), app._INTERNAL_ERROR_RESULT.get("route"))


if __name__ == "__main__":
    unittest.main()
