"""`campaign_step` — the self-cascading NI review campaign chain.

Contract pinned: make_block returns D1 dict with exact keys; _derive_paths implements C6
naming for both modes; _read_work_items reads work JSON and filters non-control ids (empty
on missing/malformed); _read_out_status reads COMPLETE/INCOMPLETE from out files. The hook
on_order_result is fail-soft: never raises, no-op when spec lacks a campaign block."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import app
import campaign_step
import config
import notes
import p20_queue
import tools


# ---------------------------------------------------------------------------
# make_block — D1 factory
# ---------------------------------------------------------------------------

class TestMakeBlock(unittest.TestCase):

    def test_make_block_returns_exact_keys(self):
        block = campaign_step.make_block(
            campaign_id="review-campaign-corrective",
            mode="corrective",
            work_dir="\\\\10.0.0.204\\SSDShared\\library-triage\\test",
            user="pintest-admin",
            step="extract",
            tier=0,
            top_attempts=0,
        )
        expected_keys = {"id", "mode", "work_dir", "user", "step", "tier", "top_attempts"}
        self.assertEqual(set(block.keys()), expected_keys)

    def test_make_block_values(self):
        block = campaign_step.make_block(
            campaign_id="cid", mode="positive", work_dir="W",
            user="alice", step="merge", tier=1, top_attempts=2)
        self.assertEqual(block["id"], "cid")
        self.assertEqual(block["mode"], "positive")
        self.assertEqual(block["work_dir"], "W")
        self.assertEqual(block["user"], "alice")
        self.assertEqual(block["step"], "merge")
        self.assertEqual(block["tier"], 1)
        self.assertEqual(block["top_attempts"], 2)

    def test_make_block_defaults(self):
        block = campaign_step.make_block("c", "m", "w", "u", "s")
        self.assertEqual(block["tier"], 0)
        self.assertEqual(block["top_attempts"], 0)


# ---------------------------------------------------------------------------
# _derive_paths — C6 naming conventions
# ---------------------------------------------------------------------------

class TestDerivePaths(unittest.TestCase):

    def test_derive_paths_corrective(self):
        W = "\\\\10.0.0.204\\SSDShared\\library-triage\\corrective"
        paths = campaign_step._derive_paths(W, "corrective")
        self.assertEqual(paths["work"],
                         "%s\\corrective.work.json" % W)
        self.assertEqual(paths["out"],
                         "%s\\corrective.review.json" % W)
        self.assertEqual(paths["shares_glob"],
                         "%s\\corrective.review.share-*.json" % W)
        self.assertEqual(paths["residual_work"],
                         "%s\\corrective.review.residual.work.json" % W)
        self.assertEqual(paths["residual_out"],
                         "%s\\corrective.review.residual.review.json" % W)
        self.assertEqual(paths["findings_md"],
                         "%s\\FINDINGS-corrective.md" % W)
        self.assertEqual(paths["findings_json"],
                         "%s\\corrective.findings.json" % W)

    def test_derive_paths_positive(self):
        W = "\\\\10.0.0.204\\SSDShared\\library-triage\\positive"
        paths = campaign_step._derive_paths(W, "positive")
        self.assertEqual(paths["work"],
                         "%s\\positive.work.json" % W)
        self.assertEqual(paths["out"],
                         "%s\\positive.review.json" % W)
        self.assertEqual(paths["shares_glob"],
                         "%s\\positive.review.share-*.json" % W)
        self.assertEqual(paths["residual_work"],
                         "%s\\positive.review.residual.work.json" % W)
        self.assertEqual(paths["residual_out"],
                         "%s\\positive.review.residual.review.json" % W)
        self.assertEqual(paths["findings_md"],
                         "%s\\FINDINGS-positive.md" % W)
        self.assertEqual(paths["findings_json"],
                         "%s\\positive.findings.json" % W)

    def test_derive_paths_returns_all_7_keys(self):
        paths = campaign_step._derive_paths("W", "corrective")
        expected = {"work", "out", "shares_glob", "residual_work",
                    "residual_out", "findings_md", "findings_json"}
        self.assertEqual(set(paths.keys()), expected)


# ---------------------------------------------------------------------------
# _read_work_items — C2 work JSON with control filtering
# ---------------------------------------------------------------------------

class TestReadWorkItems(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_read_work_items_returns_non_control_ids(self):
        work_file = self.tmp / "test.work.json"
        data = {
            "items": [
                {"id": "item-1", "control": False},
                {"id": "ctrl-1", "control": True},
                {"id": "item-2", "control": False},
                {"id": "item-3", "control": False},
            ]
        }
        work_file.write_text(json.dumps(data), encoding="utf-8")
        ids = campaign_step._read_work_items(str(work_file))
        self.assertEqual(ids, ["item-1", "item-2", "item-3"])

    def test_read_work_items_missing_file(self):
        ids = campaign_step._read_work_items(str(self.tmp / "nonexistent.work.json"))
        self.assertEqual(ids, [])

    def test_read_work_items_malformed_json(self):
        bad = self.tmp / "bad.work.json"
        bad.write_text("not json at all {{{", encoding="utf-8")
        ids = campaign_step._read_work_items(str(bad))
        self.assertEqual(ids, [])

    def test_read_work_items_no_items_key(self):
        nf = self.tmp / "empty.work.json"
        nf.write_text('{"data": 1}', encoding="utf-8")
        ids = campaign_step._read_work_items(str(nf))
        self.assertEqual(ids, [])

    def test_read_work_items_empty_items_list(self):
        ef = self.tmp / "emptylist.work.json"
        ef.write_text('{"items": []}', encoding="utf-8")
        ids = campaign_step._read_work_items(str(ef))
        self.assertEqual(ids, [])

    def test_read_work_items_all_control(self):
        cf = self.tmp / "allctrl.work.json"
        cf.write_text(json.dumps({"items": [
            {"id": "c1", "control": True},
            {"id": "c2", "control": True},
        ]}), encoding="utf-8")
        ids = campaign_step._read_work_items(str(cf))
        self.assertEqual(ids, [])


# ---------------------------------------------------------------------------
# _read_out_status — COMPLETE / INCOMPLETE from out files
# ---------------------------------------------------------------------------

class TestReadOutStatus(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_read_out_status_complete(self):
        out_file = self.tmp / "complete.review.json"
        out_file.write_text(json.dumps({"status": "COMPLETE"}), encoding="utf-8")
        status = campaign_step._read_out_status(str(out_file))
        self.assertEqual(status, "COMPLETE")

    def test_read_out_status_incomplete(self):
        out_file = self.tmp / "incomplete.review.json"
        out_file.write_text(json.dumps({"status": "INCOMPLETE"}), encoding="utf-8")
        status = campaign_step._read_out_status(str(out_file))
        self.assertEqual(status, "INCOMPLETE")

    def test_read_out_status_missing_file(self):
        status = campaign_step._read_out_status(str(self.tmp / "gone.review.json"))
        self.assertIsNone(status)

    def test_read_out_status_malformed(self):
        bad = self.tmp / "bad.review.json"
        bad.write_text("garbage", encoding="utf-8")
        status = campaign_step._read_out_status(str(bad))
        self.assertIsNone(status)

    def test_read_out_status_no_status_key(self):
        nf = self.tmp / "nostatus.review.json"
        nf.write_text('{"items": [{"id": 1}]}', encoding="utf-8")
        status = campaign_step._read_out_status(str(nf))
        self.assertIsNone(status)


# ---------------------------------------------------------------------------
# on_order_result — fail-soft hook, non-campaign is no-op
# ---------------------------------------------------------------------------

class TestHook(unittest.TestCase):

    def test_non_campaign_receipt_is_a_noop(self):
        """T1: spec without campaign block — no submission, no log entry."""
        with mock.patch.object(notes, "log") as log_mock:
            campaign_step.on_order_result({"spec": {"name": "ordinary"}}, ok=True)
        self.assertFalse(log_mock.called)

    def test_failed_step_stops_the_chain(self):
        """T2: ok=False on any campaign step — STOPPED logged with step name."""
        with mock.patch.object(notes, "log") as log_mock:
            job = {"spec": {
                "name": "test",
                "campaign": {
                    "id": "t1",
                    "mode": "corrective",
                    "step": "merge",
                    "work_dir": "",
                    "user": "",
                    "top_attempts": 0,
                },
            }}
            campaign_step.on_order_result(job, ok=False)
        log_calls = [c[0][0] for c in log_mock.call_args_list]
        self.assertTrue(
            any("STOPPED (fail-closed)" in m and "step merge failed" in m
                for m in log_calls),
            "Expected STOPPED log mentioning the failed step",
        )

    def test_hook_is_failsoft(self):
        """Any internal exception is caught — on_order_result never raises."""
        with mock.patch.object(
            campaign_step, "_derive_paths", side_effect=RuntimeError("boom")):
            with mock.patch.object(notes, "log") as log_mock:
                campaign_step.on_order_result(
                    {"spec": {"campaign": {"id": "t1"}}}, ok=True)
        # Reached here without exception — fail-soft confirmed.

    def test_extract_missing_work_file_stops(self):
        """T4: extract step with missing work file — STOPPED, no submission."""
        job = {"spec": {
            "campaign": {
                "id": "t4",
                "mode": "corrective",
                "step": "extract",
                "work_dir": "\\\\.host.\\\\shared\\\\library-triage\\\\t4",
                "user": "admin",
                "top_attempts": 0,
            },
        }}
        with mock.patch.object(tools, "_triage_container_path") as mount_mock:
            # Mount resolves, but work file path does not exist on disk
            def fake_mount(p):
                p = str(p or "")
                return pathlib.Path("/tmp/nonexistent-triage") / "mapped"
            mount_mock.side_effect = fake_mount

            with mock.patch.object(notes, "log") as log_mock:
                with mock.patch.object(
                    campaign_step, "_submit_review_chain") as submit_mock:
                    campaign_step.on_order_result(job, ok=True)

        self.assertFalse(submit_mock.called)
        log_calls = [c[0][0] for c in log_mock.call_args_list]
        self.assertTrue(
            any("STOPPED" in m and "work file missing" in m
                for m in log_calls),
            "Expected STOPPED log about missing work file",
        )

    def test_extract_absent_mount_stops(self):
        """T5: extract step with absent triage mount — STOPPED, no submission."""
        job = {"spec": {
            "campaign": {
                "id": "t5",
                "mode": "positive",
                "step": "extract",
                "work_dir": "\\\\.host.\\\\shared\\\\library-triage\\\\t5",
                "user": "admin",
                "top_attempts": 0,
            },
        }}
        with mock.patch.object(
            tools, "_triage_container_path", return_value=None):
            with mock.patch.object(notes, "log") as log_mock:
                with mock.patch.object(
                    campaign_step, "_submit_review_chain") as submit_mock:
                    campaign_step.on_order_result(job, ok=True)

        self.assertFalse(submit_mock.called)
        log_calls = [c[0][0] for c in log_mock.call_args_list]
        self.assertTrue(
            any("STOPPED" in m and "triage mount absent" in m
                for m in log_calls),
            "Expected STOPPED log about absent triage mount",
        )

    def test_residual_retry_then_exclude(self):
        """T8: residual-review INCOMPLETE with top_attempts < 2 -> resubmit;
        then second hit with top_attempts >= 2 -> exclude with exact missing ids."""
        cid = "t8"
        mode = "corrective"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\t8"
        user = "admin"

        # --- First call: top_attempts=1, INCOMPLETE -> resubmit D5 with 2 ---
        first_call = {"spec": {
            "campaign": {
                "id": cid,
                "mode": mode,
                "step": "residual-review",
                "work_dir": work_dir,
                "user": user,
                "top_attempts": 1,
            },
        }}

        paths = campaign_step._derive_paths(work_dir, mode)
        resid_out_path = paths["residual_out"]
        resid_work_path = paths["residual_work"]

        with mock.patch.object(tools, "_triage_container_path") as mount_mock:
            def fake_mount(p):
                return str(pathlib.Path("/tmp/t8-mount") / "file")
            mount_mock.side_effect = fake_mount

            # Residual out says INCOMPLETE
            with mock.patch.object(
                campaign_step, "_read_out_status", return_value="INCOMPLETE"):
                with mock.patch.object(
                    campaign_step, "_submit_residual_review") as resid_mock:
                    resid_mock.return_value = True
                    with mock.patch.object(
                        campaign_step, "_submit_exclude_run") as excl_mock:
                        campaign_step.on_order_result(first_call, ok=True)

            # Should have called _submit_residual_review with top_attempts=2
            self.assertEqual(resid_mock.call_count, 1)
            call_args = resid_mock.call_args
            # top_attempts passed as kwarg; check either way
            if len(call_args[0]) == 5:
                self.assertEqual(call_args[0][4], 2)
            else:
                self.assertEqual(call_args[1].get("top_attempts"), 2)
            # Exclude must NOT have been called yet
            self.assertFalse(excl_mock.called)

        # --- Second call: top_attempts=2, INCOMPLETE -> exclude run ---
        second_call = {"spec": {
            "campaign": {
                "id": cid,
                "mode": mode,
                "step": "residual-review",
                "work_dir": work_dir,
                "user": user,
                "top_attempts": 2,
            },
        }}

        # Work file has items [100, 101, 102]; out file has rows for [100] only
        # so missing = {101, 102}
        work_ids = set()
        row_ids_expected = set()

        with mock.patch.object(tools, "_triage_container_path") as mount_mock:
            def fake_mount(p):
                p = str(p or "")
                # residual_work path -> temp file we control
                if "residual.work" in p:
                    tmpf = pathlib.Path("/tmp/t8_resid_work.json")
                    tmpf.write_text(
                        json.dumps({"items": [
                            {"id": 100, "control": False},
                            {"id": 101, "control": False},
                            {"id": 102, "control": False},
                            {"id": "ctrl-a", "control": True},
                        ]}), encoding="utf-8")
                    work_ids.update([100, 101, 102])
                    return str(tmpf)
                # residual_out path -> temp file with only item 100
                if "residual.review" in p:
                    tmpf = pathlib.Path("/tmp/t8_resid_out.json")
                    # review outs keep rows under "results" - the WORK-file "items" key
                    # here once pinned a production bug that excluded grounded rows
                    tmpf.write_text(
                        json.dumps({
                            "status": "INCOMPLETE",
                            "results": [{"id": 100}],
                        }), encoding="utf-8")
                    row_ids_expected.add(100)
                    return str(tmpf)
                return str(pathlib.Path("/tmp/t8-mount"))

            mount_mock.side_effect = fake_mount

            with mock.patch.object(
                campaign_step, "_read_out_status", return_value="INCOMPLETE"):
                with mock.patch.object(
                    campaign_step, "_submit_residual_review") as resid_mock:
                    with mock.patch.object(
                        campaign_step, "_submit_exclude_run") as excl_mock:
                        excl_mock.return_value = True
                        campaign_step.on_order_result(second_call, ok=True)

            # Residual review must NOT be resubmitted (already at top_attempts=2)
            self.assertFalse(resid_mock.called)
            # Exclude run MUST have been called
            self.assertEqual(excl_mock.call_count, 1)
            excl_args = excl_mock.call_args[0]
            # missing_ids is the 4th positional arg (after cid, mode, work_dir, user)
            missing_ids_called = sorted(excl_args[4])
            expected_missing = sorted([101, 102])
            self.assertEqual(missing_ids_called, expected_missing,
                             "Exclude run should carry exactly the missing ids")

    def test_exclude_run_complete_submits_findings(self):
        """T9a: exclude-run COMPLETE -> findings submitted."""
        cid = "t9"
        mode = "positive"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\t9"
        user = "admin"

        job = {"spec": {
            "campaign": {
                "id": cid,
                "mode": mode,
                "step": "exclude-run",
                "work_dir": work_dir,
                "user": user,
                "top_attempts": 2,
            },
        }}

        with mock.patch.object(tools, "_triage_container_path") as mount_mock:
            mount_mock.return_value = pathlib.Path("/tmp/t9-mount")
            with mock.patch.object(
                campaign_step, "_read_out_status", return_value="COMPLETE"):
                with mock.patch.object(
                    campaign_step, "_submit_findings") as find_mock:
                    with mock.patch.object(
                        campaign_step, "_submit_exclude_run") as excl_mock:
                        campaign_step.on_order_result(job, ok=True)

        self.assertEqual(find_mock.call_count, 1)
        self.assertFalse(excl_mock.called)

    def test_exclude_run_incomplete_stops(self):
        """T9b: exclude-run still INCOMPLETE -> STOPPED log, no loop."""
        cid = "t9-stop"
        mode = "corrective"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\t9s"
        user = "admin"

        job = {"spec": {
            "campaign": {
                "id": cid,
                "mode": mode,
                "step": "exclude-run",
                "work_dir": work_dir,
                "user": user,
                "top_attempts": 2,
            },
        }}

        with mock.patch.object(tools, "_triage_container_path") as mount_mock:
            mount_mock.return_value = pathlib.Path("/tmp/t9s-mount")
            with mock.patch.object(
                campaign_step, "_read_out_status", return_value="INCOMPLETE"):
                with mock.patch.object(notes, "log") as log_mock:
                    with mock.patch.object(
                        campaign_step, "_submit_findings") as find_mock:
                        campaign_step.on_order_result(job, ok=True)

        self.assertFalse(find_mock.called)
        log_calls = [c[0][0] for c in log_mock.call_args_list]
        self.assertTrue(
            any("STOPPED" in m and "exclude run did not converge" in m
                for m in log_calls),
            "Expected STOPPED log for unconverged exclude run",
        )


# ---------------------------------------------------------------------------
# T3 \u2014 _submit_review_chain: D3 spec shape with steps
# ---------------------------------------------------------------------------

class TestSubmitReviewChain(unittest.TestCase):

    def test_review_chain_spec_shape(self):
        """T3: Verify the review chain order has correct structure per D3.

        - name is <campaign_id>-rev
        - mode compute, load gpu (from COMPUTE_PAYLOADS)
        - payload path includes native_history_review.py
        - args are [work, out]
        - steps[0] is shares with parallel=True and items
        - steps[1] is merge with after=shares, load=cpu, --merge arg, campaign block
        - FactoryOrderReq has parallel=False, items=[]
        """
        cid = "test-campaign"
        mode = "corrective"
        work_dir = "\\\\.host.\\shared\\triage\\test"
        user = "admin"
        items = ["item-a", "item-b"]

        p20_order_mock = mock.MagicMock(return_value={"ok": True, "routed_to": ["factory1"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            result = campaign_step._submit_review_chain(cid, mode, work_dir, user, items)

        self.assertTrue(result)
        self.assertEqual(p20_order_mock.call_count, 1)

        # Inspect the FactoryOrderReq passed to p20_order
        call_args = p20_order_mock.call_args
        req = call_args[0][0]
        order_spec = req.spec

        # Top-level shape
        self.assertEqual(order_spec["name"], "%s-rev" % cid)
        self.assertEqual(order_spec["mode"], "compute")
        self.assertEqual(order_spec["load"], "gpu")
        self.assertIn("native_history_review.py", order_spec["payload"])
        self.assertEqual(order_spec["notify"], "failures")

        # requires from COMPUTE_PAYLOADS entry for native_history_review
        review_requires = tools.COMPUTE_PAYLOADS["native_history_review"]["requires"]
        self.assertEqual(order_spec["requires"], list(review_requires))

        # Steps structure
        steps = order_spec["steps"]
        self.assertEqual(len(steps), 2)

        # Step 0: shares
        shares_step = steps[0]
        self.assertEqual(shares_step["name"], "shares")
        self.assertTrue(shares_step["parallel"])
        self.assertEqual(shares_step["items"], ["item-a", "item-b"])
        # shares step must NOT have a campaign block
        self.assertNotIn("campaign", shares_step)

        # Step 1: merge
        merge_step = steps[1]
        self.assertEqual(merge_step["name"], "merge")
        self.assertEqual(merge_step["after"], "shares")
        self.assertEqual(merge_step["load"], "cpu")
        paths = campaign_step._derive_paths(work_dir, mode)
        expected_merge_args = [paths["work"], paths["out"], "--merge"]
        self.assertEqual(merge_step["args"], expected_merge_args)

        # merge step MUST have the campaign block with step="merge"
        self.assertIn("campaign", merge_step)
        self.assertEqual(merge_step["campaign"]["step"], "merge")
        self.assertEqual(merge_step["campaign"]["id"], cid)

        # FactoryOrderReq-level: parallel=False, items=[] for a steps order
        self.assertFalse(req.parallel)
        self.assertEqual(req.items, [])

    def test_review_chain_returns_false_on_refusal(self):
        """When p20_order refuses, returns False and logs STOPPED."""
        p20_order_mock = mock.MagicMock(return_value={"ok": False, "why": ["no capable host"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            with mock.patch.object(notes, "log") as log_mock:
                result = campaign_step._submit_review_chain(
                    "cid", "corrective", "W", "admin", ["i1"])

        self.assertFalse(result)
        log_calls = [c[0][0] for c in log_mock.call_args_list]
        self.assertTrue(any("STOPPED" in msg for msg in log_calls))


# ---------------------------------------------------------------------------
# T6 \u2014 _submit_findings: D4 findings args shape
# ---------------------------------------------------------------------------

class TestSubmitFindings(unittest.TestCase):

    def test_findings_args_shape(self):
        """T6: Verify findings order has correct args per D4/C4.

        Args should be: [work_dir, <mode>.review, --mode, mode, --out-md, findings_md,
                        --out-json, findings_json]
        """
        cid = "test-campaign"
        mode = "positive"
        work_dir = "\\\\.host.\\shared\\triage\\test"
        user = "admin"

        p20_order_mock = mock.MagicMock(return_value={"ok": True, "routed_to": ["factory1"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            result = campaign_step._submit_findings(cid, mode, work_dir, user)

        self.assertTrue(result)
        call_args = p20_order_mock.call_args
        req = call_args[0][0]
        order_spec = req.spec

        # Name and payload
        self.assertEqual(order_spec["name"], "%s-findings" % cid)
        self.assertIn("native_history_findings.py", order_spec["payload"])
        self.assertEqual(order_spec["load"], "cpu")

        # Args shape per C4: corpus_dir base --mode mode --out-md path --out-json path
        paths = campaign_step._derive_paths(work_dir, mode)
        expected_args = [
            work_dir,
            "%s.review" % mode,
            "--mode", mode,
            "--out-md", paths["findings_md"],
            "--out-json", paths["findings_json"],
        ]
        self.assertEqual(order_spec["args"], expected_args)

        # Campaign block with step="findings"
        self.assertIn("campaign", order_spec)
        self.assertEqual(order_spec["campaign"]["step"], "findings")
        self.assertEqual(order_spec["campaign"]["id"], cid)

        # Requires from COMPUTE_PAYLOADS entry for native_history_findings
        findings_requires = tools.COMPUTE_PAYLOADS["native_history_findings"]["requires"]
        self.assertEqual(req.requires, list(findings_requires))


# ---------------------------------------------------------------------------
# T7 \u2014 _submit_residual_review: D5 requires includes triage.ctx.65536
# ---------------------------------------------------------------------------

class TestSubmitResidualReview(unittest.TestCase):

    def test_residual_requires_includes_triage_ctx(self):
        """T7: Verify residual review requires list includes 'triage.ctx.65536'.

        The D5 spec adds the escalation capability pin on top of the review entry's
        own requires (order.compute plus the lead position's model).
        """
        cid = "test-campaign"
        mode = "corrective"
        work_dir = "\\\\.host.\\shared\\triage\\test"
        user = "admin"
        top_attempts = 1

        p20_order_mock = mock.MagicMock(return_value={"ok": True, "routed_to": ["factory1"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            result = campaign_step._submit_residual_review(
                cid, mode, work_dir, user, top_attempts)

        self.assertTrue(result)
        call_args = p20_order_mock.call_args
        req = call_args[0][0]
        order_spec = req.spec

        # Name includes attempt number
        self.assertEqual(order_spec["name"], "%s-resid%d" % (cid, top_attempts))

        # Requires must include base review requires PLUS triage.ctx.65536
        review_requires = tools.COMPUTE_PAYLOADS["native_history_review"]["requires"]
        expected_requires = list(review_requires) + ["triage.ctx.65536"]
        self.assertEqual(order_spec["requires"], expected_requires)
        self.assertIn("triage.ctx.65536", order_spec["requires"])

        # Args: residual_work, residual_out (no --merge, no items)
        paths = campaign_step._derive_paths(work_dir, mode)
        self.assertEqual(order_spec["args"], [paths["residual_work"], paths["residual_out"]])

        # Campaign block with step="residual-review", tier=1
        self.assertIn("campaign", order_spec)
        self.assertEqual(order_spec["campaign"]["step"], "residual-review")
        self.assertEqual(order_spec["campaign"]["tier"], 1)
        self.assertEqual(order_spec["campaign"]["top_attempts"], top_attempts)

        # FactoryOrderReq requires also carries triage.ctx.65536
        self.assertIn("triage.ctx.65536", req.requires)


# ---------------------------------------------------------------------------
# T8/T9 \u2014 _submit_exclude_run: D6 exclude args shape
# ---------------------------------------------------------------------------

class TestSubmitExcludeRun(unittest.TestCase):

    def test_exclude_args_shape(self):
        """T8: Verify exclude run has --exclude pairs and --exclude-reason."""
        cid = "test-campaign"
        mode = "positive"
        work_dir = "\\\\.host.\\shared\\triage\\test"
        user = "admin"
        missing_ids = ["item-x", "item-y"]
        reason = "terminal: top-tier quote-grounding refused twice (campaign.step auto-exclude)"

        p20_order_mock = mock.MagicMock(return_value={"ok": True, "routed_to": ["factory1"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            result = campaign_step._submit_exclude_run(
                cid, mode, work_dir, user, missing_ids, reason)

        self.assertTrue(result)
        call_args = p20_order_mock.call_args
        req = call_args[0][0]
        order_spec = req.spec

        # Name
        self.assertEqual(order_spec["name"], "%s-exclude" % cid)

        # Args: residual_work, residual_out, --exclude id1, --exclude id2, --exclude-reason reason
        paths = campaign_step._derive_paths(work_dir, mode)
        expected_args = [paths["residual_work"], paths["residual_out"]]
        for mid in missing_ids:
            expected_args.extend(["--exclude", mid])
        expected_args.extend(["--exclude-reason", reason])

        self.assertEqual(order_spec["args"], expected_args)

        # Requires includes triage.ctx.65536 (same as D5)
        review_requires = tools.COMPUTE_PAYLOADS["native_history_review"]["requires"]
        expected_requires = list(review_requires) + ["triage.ctx.65536"]
        self.assertEqual(order_spec["requires"], expected_requires)

        # Campaign block with step="exclude-run", tier=1, top_attempts=2
        self.assertIn("campaign", order_spec)
        self.assertEqual(order_spec["campaign"]["step"], "exclude-run")
        self.assertEqual(order_spec["campaign"]["tier"], 1)
        self.assertEqual(order_spec["campaign"]["top_attempts"], 2)

    def test_exclude_run_single_missing_id(self):
        """T9 variant: exclude run with a single missing id still formats correctly."""
        cid = "test-campaign"
        mode = "corrective"
        work_dir = "\\\\.host.\\shared\\triage\\test"
        user = "admin"
        missing_ids = ["item-only"]
        reason = "some reason"

        p20_order_mock = mock.MagicMock(return_value={"ok": True, "routed_to": ["factory1"]})

        with mock.patch.object(app, "p20_order", p20_order_mock):
            result = campaign_step._submit_exclude_run(
                cid, mode, work_dir, user, missing_ids, reason)

        self.assertTrue(result)
        call_args = p20_order_mock.call_args
        order_spec = call_args[0][0].spec

        paths = campaign_step._derive_paths(work_dir, mode)
        expected_args = [
            paths["residual_work"], paths["residual_out"],
            "--exclude", "item-only",
            "--exclude-reason", "some reason",
        ]
        self.assertEqual(order_spec["args"], expected_args)


# ---------------------------------------------------------------------------
# T10 — Hook wiring: on_order_result raising must not break p20_job_result
# ---------------------------------------------------------------------------

class TestHookWiringT10(unittest.TestCase):
    """Verify that exceptions from campaign_step.on_order_result never escape
    the p20_job_result factory_order branch.  Drives the endpoint via asyncio.run
    with a fake queue record and a side_effect=Exception on the hook."""

    def _fake_request(self, token="test-token"):
        """Minimal Request-like object with the header _p20_auth checks."""
        req = mock.MagicMock()
        req.headers.get.return_value = token
        return req

    def test_t10_on_order_result_boom_does_not_break_done_path(self):
        """ok=True path: on_order_result raises → endpoint still returns ok."""
        import asyncio
        from starlette.testclient import TestClient as _unused  # noqa

        job_record = {
            "kind": "factory_order",
            "order_name": "test-order",
            "spec": {},
        }

        async def runner():
            from starlette.requests import Request as StarletteRequest

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/p20/jobs/job1/result",
                "headers": [],
            }
            req = StarletteRequest(scope)

            with mock.patch("app._p20_auth", return_value=None), \
                 mock.patch("p20_queue.get", return_value=job_record), \
                 mock.patch.object(p20_queue, "mark"), \
                 mock.patch.object(notes, "log") as log_mock, \
                 mock.patch.object(campaign_step, "on_order_result",
                                  side_effect=Exception("boom")):

                result = await app.p20_job_result(req, "job1", status="done")

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["status"], "done")
            # The fail-soft wrapper in app.py logs the hook error
            hook_logs = [
                c for c in log_mock.call_args_list
                if "NI CAMPAIGN HOOK ERROR" in str(c)
            ]
            self.assertGreaterEqual(len(hook_logs), 1)

        asyncio.run(runner())

    def test_t10_on_order_result_boom_does_not_break_failed_path(self):
        """ok=False path: on_order_result raises → endpoint still returns ok."""
        import asyncio

        job_record = {
            "kind": "factory_order",
            "order_name": "test-order",
            "spec": {},
        }

        async def runner():
            from starlette.requests import Request as StarletteRequest

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/p20/jobs/job1/result",
                "headers": [],
            }
            req = StarletteRequest(scope)

            with mock.patch("app._p20_auth", return_value=None), \
                 mock.patch("p20_queue.get", return_value=job_record), \
                 mock.patch.object(p20_queue, "mark"), \
                 mock.patch.object(notes, "log") as log_mock, \
                 mock.patch.object(campaign_step, "on_order_result",
                                  side_effect=Exception("boom")):

                result = await app.p20_job_result(req, "job1", status="failed",
                                                  error="test error")

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["status"], "failed")
            # The fail-soft wrapper in app.py logs the hook error
            hook_logs = [
                c for c in log_mock.call_args_list
                if "NI CAMPAIGN HOOK ERROR" in str(c)
            ]
            self.assertGreaterEqual(len(hook_logs), 1)

        asyncio.run(runner())


# ---------------------------------------------------------------------------
# INTEGRATION — p20_job_result → on_order_result → submission builders
# Exercises the real production call path with app.p20_order mocked at the
# top level.  Verifies that a fake job result for 'extract' completes triggers
# a review_chain submission, and that 'merge' completing triggers findings.
# ---------------------------------------------------------------------------

class TestIntegrationExtractToReviewChain(unittest.TestCase):
    """Integration: fire p20_job_result with status='done' for an extract step;
    verify on_order_result triggers _submit_review_chain and captures D3 shape."""

    def _fake_request(self):
        import starlette.requests
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/p20/jobs/job1/result",
            "headers": [],
        }
        return starlette.requests.Request(scope)

    def test_extract_done_submits_review_chain(self):
        """Fire extract done via the real endpoint; verify p20_order is called with
        a D3 review-chain spec (two steps: shares + merge with campaign block)."""
        import asyncio

        cid = "int-extract-test"
        mode = "corrective"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\integ"
        user = "admin"

        job_record = {
            "kind": "factory_order",
            "order_name": "%s-ext" % cid,
            "spec": {
                "name": "%s-ext" % cid,
                "campaign": campaign_step.make_block(
                    campaign_id=cid,
                    mode=mode,
                    work_dir=work_dir,
                    user=user,
                    step="extract",
                ),
            },
        }

        # Create a temp work file with non-control items
        import tempfile
        tmpdir = tempfile.mkdtemp()
        container_work = pathlib.Path(tmpdir) / "corrective.work.json"
        container_work.write_text(
            json.dumps({"items": [
                {"id": "item-1", "control": False},
                {"id": "ctrl-1", "control": True},
                {"id": "item-2", "control": False},
            ]}),
            encoding="utf-8",
        )

        captured_orders = []

        async def runner():
            with mock.patch("app._p20_auth", return_value=None), \
                 mock.patch("p20_queue.get", return_value=job_record), \
                 mock.patch.object(p20_queue, "mark"), \
                 mock.patch.object(notes, "log"):

                # Mock _triage_container_path to map work_dir paths to our temp files
                original_truncate = None
                unc_to_tmp = {}
                real_paths = campaign_step._derive_paths(work_dir, mode)

                def fake_mount(unc_path):
                    p = str(unc_path or "")
                    if p not in unc_to_tmp:
                        # Map the work file to our temp file; everything else maps
                        # to a harmless path
                        for derived_key, derived_unc in real_paths.items():
                            if derived_key == "work" and derived_unc == p:
                                unc_to_tmp[p] = str(container_work)
                                return str(container_work)
                        unc_to_tmp[p] = tmpdir + "/mapped"
                    return unc_to_tmp.get(p, tmpdir + "/fallback")

                with mock.patch.object(tools, "_triage_container_path",
                                      side_effect=fake_mount):

                    # Mock app.p20_order to capture the order and return success
                    def capture_p20(req):
                        captured_orders.append(req)
                        return {"ok": True, "routed_to": ["factory1"]}

                    with mock.patch.object(app, "p20_order", side_effect=capture_p20):
                        result = await app.p20_job_result(
                            self._fake_request(), "job1", status="done")

            # Endpoint returns ok=True regardless of hook outcome
            self.assertEqual(result["ok"], True)
            # At least one order was captured (the review chain from extract)
            self.assertGreaterEqual(len(captured_orders), 1)

            req = captured_orders[0]
            order_spec = req.spec

            # D3 shape verification:
            # - name ends with -rev
            self.assertEqual(order_spec["name"], "%s-rev" % cid)
            self.assertEqual(order_spec["mode"], "compute")
            self.assertIn("native_history_review.py", order_spec["payload"])

            # args should be [work, out] at the top level
            paths = campaign_step._derive_paths(work_dir, mode)
            self.assertEqual(order_spec["args"], [paths["work"], paths["out"]])

            # Steps: shares then merge
            steps = order_spec["steps"]
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0]["name"], "shares")
            self.assertTrue(steps[0]["parallel"])
            self.assertEqual(steps[0]["items"], ["item-1", "item-2"])

            self.assertEqual(steps[1]["name"], "merge")
            self.assertEqual(steps[1]["after"], "shares")
            self.assertEqual(steps[1]["load"], "cpu")
            expected_merge_args = [paths["work"], paths["out"], "--merge"]
            self.assertEqual(steps[1]["args"], expected_merge_args)

            # Campaign block ONLY on merge step, not on shares
            self.assertIn("campaign", steps[1])
            self.assertEqual(steps[1]["campaign"]["step"], "merge")
            self.assertNotIn("campaign", steps[0])

            # FactoryOrderReq: parallel=False, items=[] for a steps order
            self.assertFalse(req.parallel)
            self.assertEqual(req.items, [])

        asyncio.run(runner())

    def test_merge_done_submits_findings(self):
        """Fire merge done via the real endpoint with COMPLETE review;
        verify p20_order is called with a D4 findings spec.

        The job record's spec carries the step-level campaign block (after
        compose_steps would have extracted it), not a nested steps array."""
        import asyncio

        cid = "int-merge-test"
        mode = "positive"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\integ-merge"
        user = "admin"

        # After compose_steps, each step is enqueued with its own spec that
        # includes the campaign block at the top level (not nested in steps).
        merge_campaign = campaign_step.make_block(
            campaign_id=cid,
            mode=mode,
            work_dir=work_dir,
            user=user,
            step="merge",
        )
        job_record = {
            "kind": "factory_order",
            "order_name": "%s-rev" % cid,
            "spec": {
                "name": "merge",
                "campaign": merge_campaign,
            },
        }

        import tempfile
        tmpdir = tempfile.mkdtemp()

        captured_orders = []

        async def runner():
            with mock.patch("app._p20_auth", return_value=None), \
                 mock.patch("p20_queue.get", return_value=job_record), \
                 mock.patch.object(p20_queue, "mark"):

                # Map work_dir and derived paths to temp locations
                real_paths = campaign_step._derive_paths(work_dir, mode)
                path_map = {
                    work_dir: tmpdir,
                }
                for key in real_paths:
                    path_map[real_paths[key]] = tmpdir + "/mapped-" + key

                def fake_mount(unc_path):
                    p = str(unc_path or "")
                    return path_map.get(p, tmpdir + "/fallback")

                with mock.patch.object(tools, "_triage_container_path",
                                      side_effect=fake_mount):

                    # Force out status to COMPLETE so findings gets submitted
                    with mock.patch.object(campaign_step, "_read_out_status",
                                          return_value="COMPLETE"):

                        def capture_p20(req):
                            captured_orders.append(req)
                            return {"ok": True, "routed_to": ["factory1"]}

                        with mock.patch.object(notes, "log"), \
                             mock.patch.object(app, "p20_order", side_effect=capture_p20):
                            result = await app.p20_job_result(
                                self._fake_request(), "job1", status="done")

            self.assertEqual(result["ok"], True)
            self.assertGreaterEqual(len(captured_orders), 1)

            req = captured_orders[0]
            order_spec = req.spec

            # D4 findings shape verification:
            self.assertEqual(order_spec["name"], "%s-findings" % cid)
            self.assertIn("native_history_findings.py", order_spec["payload"])
            self.assertEqual(order_spec["load"], "cpu")

            paths = campaign_step._derive_paths(work_dir, mode)
            expected_args = [
                work_dir,
                "positive.review",
                "--mode", "positive",
                "--out-md", paths["findings_md"],
                "--out-json", paths["findings_json"],
            ]
            self.assertEqual(order_spec["args"], expected_args)

            # Campaign block with step="findings"
            self.assertIn("campaign", order_spec)
            self.assertEqual(order_spec["campaign"]["step"], "findings")
            self.assertEqual(order_spec["campaign"]["id"], cid)

        asyncio.run(runner())


class TestIntegrationMergeResidual(unittest.TestCase):
    """Integration: fire merge done with INCOMPLETE review + residual work;
    verify residual review (D5) is submitted instead of findings."""

    def _fake_request(self):
        import starlette.requests
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/p20/jobs/job1/result",
            "headers": [],
        }
        return starlette.requests.Request(scope)

    def test_merge_incomplete_with_residual_submits_resid(self):
        """Merge done, review is INCOMPLETE but residual work exists → D5 submitted."""
        import asyncio

        cid = "int-resid-test"
        mode = "corrective"
        work_dir = "\\\\.host.\\\\shared\\\\library-triage\\\\integ-resid"
        user = "admin"

        merge_campaign = campaign_step.make_block(
            campaign_id=cid,
            mode=mode,
            work_dir=work_dir,
            user=user,
            step="merge",
            top_attempts=0,
        )
        job_record = {
            "kind": "factory_order",
            "order_name": "%s-rev" % cid,
            "spec": {
                "name": "merge",
                "campaign": merge_campaign,
            },
        }

        import tempfile
        tmpdir = tempfile.mkdtemp()

        # Residual work file — must exist on disk for pathlib.Path.exists() check
        real_paths = campaign_step._derive_paths(work_dir, mode)
        container_resid_work = pathlib.Path(tmpdir) / "residual.work"
        container_resid_work.write_text(
            json.dumps({"items": []}),
            encoding="utf-8",
        )

        captured_orders = []

        async def runner():
            with mock.patch("app._p20_auth", return_value=None), \
                 mock.patch("p20_queue.get", return_value=job_record), \
                 mock.patch.object(p20_queue, "mark"):

                path_map = {
                    work_dir: tmpdir,
                    real_paths["out"]: tmpdir + "/out.review.json",
                    real_paths["residual_work"]: str(container_resid_work),
                }

                def fake_mount(unc_path):
                    p = str(unc_path or "")
                    return path_map.get(p, tmpdir + "/fallback")

                with mock.patch.object(tools, "_triage_container_path",
                                      side_effect=fake_mount):

                    # Force out status to INCOMPLETE so residual gets submitted
                    with mock.patch.object(campaign_step, "_read_out_status",
                                          return_value="INCOMPLETE"):

                        def capture_p20(req):
                            captured_orders.append(req)
                            return {"ok": True, "routed_to": ["factory1"]}

                        with mock.patch.object(notes, "log"), \
                             mock.patch.object(app, "p20_order", side_effect=capture_p20):
                            result = await app.p20_job_result(
                                self._fake_request(), "job1", status="done")

            self.assertEqual(result["ok"], True)
            self.assertGreaterEqual(len(captured_orders), 1)

            req = captured_orders[0]
            order_spec = req.spec

            # D5 shape: name includes resid, payload is review, requires triage.ctx.65536
            self.assertIn("resid", order_spec["name"])
            self.assertIn("native_history_review.py", order_spec["payload"])
            self.assertIn("triage.ctx.65536", order_spec["requires"])

            # Campaign block with step="residual-review", tier=1
            self.assertEqual(order_spec["campaign"]["step"], "residual-review")
            self.assertEqual(order_spec["campaign"]["tier"], 1)

        asyncio.run(runner())


if __name__ == "__main__":
    unittest.main()
