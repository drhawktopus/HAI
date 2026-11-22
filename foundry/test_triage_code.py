"""triage_code drafts with the weighed row's own settings through the factory's shared call (infer_ollama._chat):
the row's thinking level and temperature reach the model, num_predict equals the window, and no thinking flag means
thinking off."""
import base64
import json
import os
import tempfile
import unittest
from unittest import mock

import infer_ollama
import triage_code


def _work(path):
    item = {"id": "0001", "kind": "comment", "file": "x.py", "lines": "1-1",
            "text_b64": base64.b64encode(b"# a note").decode(), "context_b64": base64.b64encode(b"x = 1").decode()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"items": [item]}, f)


class TheRowsSettingsReachTheModel(unittest.TestCase):
    def run_draft(self, *flags):
        d = tempfile.mkdtemp(prefix="tc-")
        work, out = os.path.join(d, "w.json"), os.path.join(d, "o.json")
        _work(work)
        reply = {"model": "m", "eval_count": 5,
                 "message": {"content": json.dumps({"disposition": "CUT", "new_text": "", "note": "n"})}}
        with mock.patch.object(infer_ollama, "_chat", return_value=reply) as chat, \
                mock.patch.object(triage_code, "_assert_resident"):
            triage_code.main([work, out, "--model", "m", "--num-ctx", "8192"] + list(flags))
        with open(out, encoding="utf-8") as f:
            return chat.call_args, json.load(f)

    def test_a_thinking_level_and_temperature_are_sent(self):
        call, out = self.run_draft("--think-level", "xhigh", "--temperature", "0.6")
        self.assertEqual(call.kwargs["think"], "xhigh")
        self.assertEqual(call.args[3]["temperature"], 0.6)
        self.assertEqual(call.args[3]["num_predict"], 8192)
        self.assertEqual(out["drafted"], 1)

    def test_no_flag_is_thinking_off(self):
        call, _out = self.run_draft()
        self.assertFalse(call.kwargs["think"])
        self.assertNotIn("temperature", call.args[3])


def _work2(path):
    items = [{"id": "000%d" % n, "kind": "comment", "file": "x.py", "lines": "1-1",
              "text_b64": base64.b64encode(b"# a note").decode(), "context_b64": base64.b64encode(b"x = 1").decode()}
             for n in (1, 2, 3)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f)


class EveryDraftLandsOnDiskAsItIsMade(unittest.TestCase):
    """A run that is stopped keeps the drafts it made; the next run of the same order reads them back and drafts only
    what is missing; the sidecar is gone once the whole drafts file is written."""
    REPLY = {"model": "m", "eval_count": 5,
             "message": {"content": json.dumps({"disposition": "KEEP-CLEAN", "new_text": "# a rule", "note": "n"})}}

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tc-")
        self.work, self.out = os.path.join(self.d, "w.json"), os.path.join(self.d, "o.json")
        _work2(self.work)
        self.argv = [self.work, self.out, "--model", "m", "--num-ctx", "8192", "--breaker", "1"]

    def test_a_stopped_run_keeps_its_drafts_and_the_next_run_finishes_them(self):
        calls = []

        def first_run(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("the box went away")      # the second draft fails and the breaker stops the run
            return self.REPLY
        with mock.patch.object(infer_ollama, "_chat", side_effect=first_run), \
                mock.patch.object(triage_code, "_assert_resident"):
            with self.assertRaises(SystemExit) as stop:
                triage_code.main(self.argv)
        self.assertEqual(stop.exception.code, 3, "a stopped run exits 3, so the desk never marks it done")
        self.assertFalse(os.path.exists(self.out), "nothing is shipped as the whole")
        partial = self.out + ".partial.jsonl"
        self.assertTrue(os.path.exists(partial), "the sidecar holds what was made before the stop")
        rows = [json.loads(l) for l in open(partial, encoding="utf-8") if l.strip()]
        self.assertEqual([r["id"] for r in rows], ["0001", "0002"])
        self.assertEqual(rows[0]["disposition"], "KEEP-CLEAN")
        self.assertIn("error", rows[1])
        # the second run: the one good draft is read back, the failed one and the untouched one are drafted
        with mock.patch.object(infer_ollama, "_chat", return_value=self.REPLY) as chat, \
                mock.patch.object(triage_code, "_assert_resident"):
            triage_code.main(self.argv)
        self.assertEqual(chat.call_count, 1, "only the item with no draft on disk is sent to the model")
        with open(self.out, encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual([r["id"] for r in out["results"]], ["0001", "0002", "0003"])
        self.assertEqual(out["drafted"], 2)
        self.assertEqual(out["errors"], 1, "the failed row is kept as it was, for the review to see")
        self.assertFalse(os.path.exists(partial), "the whole drafts file now holds every row")


if __name__ == "__main__":
    unittest.main()
