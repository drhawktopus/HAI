"""The reviewer must fail SOFT; a silent skip must never be reported as a clean result."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import claim_review
import claim_sweep


class ParseTests(unittest.TestCase):
    def test_valid(self):
        out = claim_review._parse('{"unbacked":[{"claim":"sam has worse specs","why":"no source"}],"note":"x"}')
        self.assertEqual(len(out["unbacked"]), 1)
        self.assertEqual(out["unbacked"][0]["claim"], "sam has worse specs")

    def test_tolerates_surrounding_noise(self):
        out = claim_review._parse('thinking... {"unbacked":[],"note":""} trailing')
        self.assertEqual(out["unbacked"], [])

    def test_rejects_non_json(self):
        self.assertIsNone(claim_review._parse("not json at all"))

    def test_rejects_wrong_shape(self):
        # `unbacked` must be a LIST. A model answering with a string must not read as "none found".
        self.assertIsNone(claim_review._parse('{"unbacked":"none","note":""}'))

    def test_bounded(self):
        many = ",".join('{"claim":"c%d","why":"w"}' % i for i in range(40))
        out = claim_review._parse('{"unbacked":[%s],"note":""}' % many)
        self.assertLessEqual(len(out["unbacked"]), 12)   # a wall of findings is a muted reviewer

    def test_an_answer_filed_under_thinking_is_still_read(self):
        out = claim_review.parse_answer("", '{"unbacked":[{"claim":"c","why":"w"}],"note":""}')
        self.assertEqual(len(out["unbacked"]), 1)


class DiffTests(unittest.TestCase):
    def test_additions_kept_deletions_dropped(self):
        d = claim_review.build_diff("old line\n", "new line\n", "doc.md")
        self.assertIn("new line", d)
        self.assertNotIn("-old line", d)

    def test_bounded_length(self):
        big = "\n".join("added line %d" % i for i in range(5000))
        d = claim_review.build_diff("", big, "doc.md", max_chars=500)
        self.assertLessEqual(len(d), 500)

    def test_a_typo_fix_is_not_worth_a_review_and_disabled_reviews_nothing(self):
        with patch("claim_review.config.CLAIM_REVIEW_ENABLED", True), \
             patch("claim_review.config.CLAIM_REVIEW_MIN_DIFF", 300):
            self.assertFalse(claim_review.worth_review("typo"))
            self.assertTrue(claim_review.worth_review("x" * 300))
        with patch("claim_review.config.CLAIM_REVIEW_ENABLED", False):
            self.assertFalse(claim_review.worth_review("x" * 999))


CLAIM_TEXT = "The service on the box now answers on its new port and every client was moved over.\n" * 8


class FakeDesk:
    """The desk's two in-process doors the sweep uses: p20_order (records the order, replies as configured) and
    _order_status_view (the order's fate)."""

    def __init__(self, reply=None):
        self.orders, self.status = [], {}
        self.reply = reply or {"ok": True, "steps": [{"id": "ord-1", "host": "bighonker"}]}

    def FactoryOrderReq(self, **kw):                                 # noqa: N802 - the app's name
        return kw

    def p20_order(self, req):
        self.orders.append(req)
        return self.reply

    def _order_status_view(self, oid):
        return self.status.get(oid) or {"ok": False, "error": "no such order id"}


class _Sweep(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        docs, state = os.path.join(self.d, "docs"), os.path.join(self.d, "state")
        os.makedirs(docs)
        self.order_dir = os.path.join(self.d, "share")
        self.docs = docs
        # DOCS_DIR must be patched to prevent sweep() from walking the live library; the live jobs share must also be isolated.
        for x in (patch("claim_sweep.DIRS", [docs]), patch.object(claim_sweep.config, "DOCS_DIR", docs),
                  patch("claim_sweep.SNAP_DIR", os.path.join(state, "snapshots")),
                  patch("claim_sweep.FINDINGS_DIR", os.path.join(state, "findings")),
                  patch("claim_sweep.PENDING", os.path.join(state, "pending.json")),
                  patch("claim_sweep.STAGED_DIR", os.path.join(state, "staged")),
                  patch("claim_sweep.ORDER_LOCAL", self.order_dir),
                  patch("claim_review.config.CLAIM_REVIEW_ENABLED", True),
                  patch("claim_review.config.CLAIM_REVIEW_MIN_DIFF", 300)):
            x.start()
            self.addCleanup(x.stop)
        self.said = []
        p = patch("claim_sweep.notify_desk", side_effect=self.said.append)
        p.start()
        self.addCleanup(p.stop)
        self.a = self.doc("a.md", CLAIM_TEXT, "the old text\n")

    def doc(self, name, now, before):
        path = os.path.join(self.docs, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(now)
        os.makedirs(claim_sweep.SNAP_DIR, exist_ok=True)
        with open(claim_sweep._snap_path(path), "w", encoding="utf-8") as f:   # an earlier snapshot: not a baseline
            f.write(before)
        return path

    def snap(self, path):
        with open(claim_sweep._snap_path(path), encoding="utf-8") as f:
            return f.read()

    def deliver_gpu(self, rows):
        pend = json.load(open(claim_sweep.PENDING, encoding="utf-8"))
        with open(pend["paths"]["out"], "w", encoding="utf-8") as f:
            json.dump({"rows": rows}, f)
        return pend


class TheReviewIsAnOrder(_Sweep):
    def test_the_sweep_submits_ONE_background_order_and_makes_no_model_call(self):
        desk = FakeDesk()
        with patch("ollama_client.post_supervised") as call:
            out = claim_sweep.sweep(desk=desk)
        call.assert_not_called()
        self.assertEqual(len(desk.orders), 1)
        o = desk.orders[0]
        self.assertEqual((o["family"], o["tier"], o["urgency"], o["kind"]),
                         ("work-w1", "fellowship", "background", "review"))
        self.assertNotIn("only", o, "the desk weighs every row - nothing pins the review to a box")
        self.assertEqual(o["spec"]["payload"], "{FOUNDRY_ROOT}\\bin\\infer_ollama.py")
        for tok in ("{POLICY_MODEL}", "{POLICY_NUM_CTX}", "{POLICY_THINK_ARGS}", "{POLICY_OFFLOAD_ARGS}"):
            self.assertIn(tok, o["spec"]["args"], "the weighed row sets the model and its settings")
        self.assertEqual(o["fellowship"]["spec"]["tasks"], ["d001"])
        items = json.load(open(os.path.join(self.order_dir, os.listdir(self.order_dir)[0],
                                            out["name"] + ".items.json"), encoding="utf-8"))["items"]
        self.assertEqual(items[0]["system"], claim_review._SYSTEM)
        # The engine's grammar ensures JSON validity; the schema is used directly to avoid escaping issues.
        self.assertEqual(items[0]["format"], claim_review.SCHEMA)
        # the list is the WHOLE schema: a `note` key came out first and the model wrote "all backed" before looking
        self.assertEqual(list(claim_review.SCHEMA["properties"]), ["unbacked"])
        self.assertEqual(claim_review._parse('{"unbacked": []}'), {"unbacked": [], "note": ""}, "note is optional")
        self.assertIn("a.md", items[0]["prompt"])
        import time as _t
        self.assertTrue(items[0]["prompt"].startswith("(Today is ") and _t.strftime("%Y") in items[0]["prompt"][:60],
                        "the reviewer is told today's date - a dated proof is not 'in the future'")
        self.assertEqual(self.snap(self.a), "the old text\n", "nothing advances until the review comes back")
        self.assertEqual(out["submitted"], "ord-1")

    def test_a_done_order_writes_findings_and_advances_only_what_was_reviewed(self):
        b = self.doc("b.md", CLAIM_TEXT.replace("port", "address"), "older b\n")
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        self.deliver_gpu([{"id": "d001", "answer": '{"unbacked":[{"claim":"the new port","why":"no source"}]}'},
                          {"id": "d002", "answer": "I could not decide"}])
        desk.status["ord-1"] = {"ok": True, "status": "done", "host": "bighonker"}
        res = claim_sweep.collect(desk)
        self.assertEqual(res["reviewed"], 1)
        self.assertEqual(self.snap(self.a), CLAIM_TEXT, "the reviewed text is the new baseline")
        self.assertEqual(self.snap(b), "older b\n", "a doc with no usable answer stays for the next sweep")
        self.assertTrue(any("1 unbacked claim" in m and "b.md" in m and "bighonker" in m for m in self.said), self.said)
        self.assertFalse(os.path.exists(claim_sweep.PENDING))

    def test_a_pending_order_is_never_stacked_and_says_so(self):
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        for st in ("pending", "held", "running"):
            desk.status["ord-1"] = {"ok": True, "status": st, "host": ""}
            out = claim_sweep.sweep(desk=desk)
            self.assertTrue(out.get("skipped"))
        self.assertEqual(len(desk.orders), 1)
        self.assertTrue(any("DID NOT SUBMIT" in m and "NOT a clean result" in m for m in self.said))

    def test_a_failed_order_is_not_a_clean_result_and_the_next_sweep_reviews_it(self):
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        desk.status["ord-1"] = {"ok": True, "status": "failed", "host": "bighonker", "reason": "boom"}
        claim_sweep.sweep(desk=desk)
        self.assertTrue(any("ended failed" in m and "NOT a clean result" in m for m in self.said), self.said)
        self.assertEqual(self.snap(self.a), "the old text\n")
        self.assertEqual(len(desk.orders), 2, "the next sweep reviews the change again")

    def test_a_refused_order_announces_that_it_did_not_run(self):
        """The bug this guards: a sweep that reviews nothing must NOT read as a clean library."""
        desk = FakeDesk({"ok": False, "error": "no factory can build it"})
        out = claim_sweep.sweep(desk=desk)
        self.assertTrue(out.get("skipped"))
        self.assertTrue(any("DID NOT RUN" in m and "NOT a clean result" in m for m in self.said))
        self.assertEqual(self.snap(self.a), "the old text\n")
        self.assertFalse(os.path.exists(claim_sweep.PENDING))

    def test_a_held_order_waits_at_the_desk(self):
        """Background work waits for a box - nothing here routes around a hold."""
        desk = FakeDesk({"ok": True, "held": True, "id": "ord-h", "why": ["every capable card busy"]})
        out = claim_sweep.sweep(desk=desk)
        self.assertEqual(out["submitted"], "ord-h")
        self.assertEqual(json.load(open(claim_sweep.PENDING, encoding="utf-8"))["id"], "ord-h")

    def test_a_late_delivery_gets_one_more_look_then_is_reported(self):
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        desk.status["ord-1"] = {"ok": True, "status": "done", "host": "bighonker"}
        self.assertEqual(claim_sweep.collect(desk).get("pending"), "delivery not on the share yet")
        self.assertEqual(claim_sweep.collect(desk).get("undelivered"), "no out.json on the share")
        self.assertTrue(any("delivered nothing usable" in m for m in self.said))

    def test_a_fellowship_delivery_needs_its_control(self):
        desk = FakeDesk({"ok": True, "steps": [{"id": "ord-f", "host": "fellowship"}]})
        claim_sweep.sweep(desk=desk)
        pend = json.load(open(claim_sweep.PENDING, encoding="utf-8"))
        with open(pend["paths"]["results"], "w", encoding="utf-8") as f:
            json.dump({"control_passed": False, "rows": [{"task": "d001", "answer": '{"unbacked":[]}'}]}, f)
        desk.status["ord-f"] = {"ok": True, "status": "done", "host": "fellowship"}
        self.assertIn("control did not pass", claim_sweep.collect(desk)["undelivered"])
        self.assertEqual(self.snap(self.a), "the old text\n")

    def test_the_receipt_collects_only_its_own_order(self):
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        with patch("claim_sweep.collect") as col:
            claim_sweep.on_order_result({"id": "someone-else"})
            col.assert_not_called()
            claim_sweep.on_order_result({"id": "ord-1"})
            col.assert_called_once()

    def test_clean_sweep_still_reports(self):
        """Silence must never be able to mean 'the sweep is broken'."""
        os.remove(self.a)
        self.doc("t.md", "a typo fixed\n", "a typo fxied\n")
        desk = FakeDesk()
        claim_sweep.sweep(desk=desk)
        self.assertEqual(desk.orders, [], "a typo fix is not worth an order")
        self.assertTrue(any(m.startswith("clean") for m in self.said), self.said)

    def test_the_first_sweep_is_a_baseline_and_orders_nothing(self):
        shutil.rmtree(claim_sweep.SNAP_DIR)
        desk = FakeDesk()
        out = claim_sweep.sweep(desk=desk)
        self.assertTrue(out.get("baseline"))
        self.assertEqual(desk.orders, [])


class SweepDoorRunsInKirksProcess(unittest.TestCase):
    """Sweep endpoint constraints: worker token required; single active sweep instance."""

    def setUp(self):
        from fastapi.testclient import TestClient
        import app
        self.app, self.client = app, TestClient(app.app)

    def test_the_door_needs_the_token(self):
        cfg = claim_sweep.config
        with patch.object(cfg, "P20_WORKER_TOKEN", "tok"), patch("claim_sweep.sweep", return_value={"x": 1}) as sw:
            self.assertEqual(self.client.post("/claims/sweep").status_code, 403, "no token, no sweep")
            sw.assert_not_called()
            r = self.client.post("/claims/sweep", headers={"X-P20-Token": "tok"})
        self.assertEqual(r.json(), {"x": 1})

    def test_one_sweep_at_a_time(self):
        cfg = claim_sweep.config
        self.app._CLAIM_SWEEP_LOCK.acquire()
        try:
            with patch.object(cfg, "P20_WORKER_TOKEN", "tok"), patch("claim_sweep.sweep") as sw:
                r = self.client.post("/claims/sweep", headers={"X-P20-Token": "tok"})
        finally:
            self.app._CLAIM_SWEEP_LOCK.release()
        self.assertEqual(r.json(), {"already_running": True})
        sw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
