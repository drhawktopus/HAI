"""Client boundary pins for the Project 12 ordered independence eval harness."""
import inspect
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import hai_independence_evals as harness


class OrderedHarness(unittest.TestCase):
    def test_load_prompt_resolves_the_pin_profile_and_keeps_the_turn_verbatim(self):
        with tempfile.TemporaryDirectory(prefix="eval-history-") as td:
            root = pathlib.Path(td)
            source = root / "ClaudeSupervisor" / "control.json"
            source.parent.mkdir()
            source.write_text(json.dumps([
                {"role": "user", "content": "first  prompt\nkept byte-verbatim"},
                {"role": "assistant", "content": "answer"},
            ]), encoding="utf-8")
            pin = {"id": "E5", "profile": "ClaudeSupervisor", "src": "control.json", "turn": 0}
            with mock.patch.object(harness, "HISTORY_ROOT", root):
                prompt = harness.load_prompt(pin)
        self.assertEqual(prompt, "first  prompt\nkept byte-verbatim")

    def test_submit_composes_one_complete_order_without_host_or_routing(self):
        pins = [dict(harness.PINS[0]), dict(harness.PINS[-1])]
        prompts = {pin["id"]: "prompt-" + pin["id"] for pin in pins}
        seen = {}

        def fake_request(path, *, payload=None, timeout=30):
            seen.update({"path": path, "payload": payload, "timeout": timeout})
            return {"ok": True, "id": "campaign123", "order": "ordered", "total": 2}

        with mock.patch.object(harness, "load_prompt",
                               side_effect=lambda pin: prompts[pin["id"]]), \
             mock.patch.object(harness, "_json_request", side_effect=fake_request):
            out = harness.submit_eval_order(pins, "project 12 close")

        self.assertEqual(out["id"], "campaign123")
        self.assertEqual(seen["path"], "/p20/order")
        spec = seen["payload"]["spec"]
        self.assertEqual(spec["kind"], "eval_campaign")
        self.assertEqual(spec["user"], harness.ORDER_USER)
        self.assertEqual(spec["eval_user"], harness.EVAL_USER)
        self.assertEqual(spec["control"]["pin"], "E5")
        self.assertTrue(spec["evidence"]["require_served_identity"])
        self.assertTrue(spec["evidence"]["require_deep_trace"])
        self.assertTrue(spec["evidence"]["void_on_ctx_downgrade"])
        self.assertNotIn("host", spec)
        self.assertNotIn("routing", json.dumps(spec))
        self.assertEqual([pin["id"] for pin in spec["pins"]], ["E1", "E5"])
        self.assertEqual(spec["pins"][0]["prompt_sha256"], harness.sha(prompts["E1"]))

    def test_submit_refuses_a_campaign_without_the_positive_control(self):
        with mock.patch.object(harness, "_json_request") as request:
            with self.assertRaises(harness.EvalError):
                harness.submit_eval_order([dict(harness.PINS[0])], "missing-control")
        request.assert_not_called()

    def test_wait_only_reads_the_order_product(self):
        views = iter([
            {"ok": True, "id": "abc", "status": "running", "progress": 0, "total": 1,
             "active_pin": "E5", "hold_reason": ""},
            {"ok": True, "id": "abc", "status": "done", "progress": 1, "total": 1,
             "active_pin": "", "hold_reason": "", "rows": [{"id": "E5"}]},
        ])
        paths = []

        def fake_request(path, *, payload=None, timeout=30):
            paths.append((path, payload))
            return next(views)

        with mock.patch.object(harness, "_json_request", side_effect=fake_request), \
             mock.patch.object(harness.time, "sleep", lambda *_a: None):
            view = harness.wait_for_eval_order("abc")
        self.assertEqual(view["status"], "done")
        self.assertEqual(paths, [
            ("/p20/eval/abc?user=ClaudeSupervisor", None),
            ("/p20/eval/abc?user=ClaudeSupervisor", None),
        ])

    def test_grade_happens_after_read_and_writes_a_client_artifact(self):
        pin = dict(harness.PINS[-1])
        row = {
            "id": "E5",
            "measurement_state": "clean",
            "answer": "decoder",
            "route": "heavyweight",
            "reported_host": "Mothership",
            "reported_model": "model27",
            "deep_trace": {"schema": "deep_trace_v1"},
            "work_journal": [{"phase": "first_pass"}],
            "transcript": [{"tool": "get_document"}],
            "placement": {"host": "mothership"},
            "execution_evidence": [{"served_model": "model27"}],
        }
        with tempfile.TemporaryDirectory(prefix="eval-client-results-") as td, \
             mock.patch.object(harness, "RESULTS", pathlib.Path(td)), \
             mock.patch.dict(harness.GRADERS,
                             {"E5": lambda _resp, _answer: ("PASS", "positive control")}), \
             mock.patch.object(harness, "_json_request") as request:
            rc = harness.grade_order_product(
                [pin], {"id": "abc", "status": "done", "rows": [row]}, "label / unsafe")
            files = list(pathlib.Path(td).glob("*.json"))
        self.assertEqual(rc, 0)
        request.assert_not_called()
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0].name)

    def test_source_contains_no_client_placement_or_direct_query_lane(self):
        source = inspect.getsource(harness)
        self.assertNotIn("import placement", source)
        self.assertNotIn("def require_idle(", source)
        self.assertNotIn("def serving_tier(", source)
        self.assertNotIn("def ask(", source)
        self.assertNotIn('"/query"', source)
        self.assertIn('"/p20/order"', source)
        self.assertIn('"/p20/eval/', source)


if __name__ == "__main__":
    unittest.main()
