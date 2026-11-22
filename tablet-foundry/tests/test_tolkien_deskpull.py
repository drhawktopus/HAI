"""The fleet-desk pull (30b): the desk HOLDS, Tolkien ASKS, claims BEFORE writing, stages
ATOMICALLY, and reports completion truthfully. Transport is faked at `_desk_call` -- the HTTP
shape is the GPU foundry's proven one; what these pin is the ORDER of operations and the
failure behaviour, which is where a pull goes wrong (claim-after-write puts one spec on two
factories; a half-written spec reads as a broken order and gets archived)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as TF  # noqa: E402


def _job(jid="j1", name="probe-order", kind="factory_order", spec=None):
    return {"id": jid, "kind": kind, "order_name": name,
            "spec": spec if spec is not None else {"name": name, "items": ["a"]}}


class CollectFleetOrders(unittest.TestCase):

    def setUp(self):
        self.qdir = tempfile.mkdtemp(prefix="deskpull-")
        self.patches = [
            mock.patch.object(TF, "QUEUE_DIR", self.qdir),
            mock.patch.object(TF, "P20_TOKEN", "tok"),
        ]
        for p in self.patches:
            p.start()
        TF._FLEET_DESK_QUIET[0] = False

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.qdir, ignore_errors=True)

    def _wire(self, jobs, claim_ok=True, calls=None):
        def fake(path, method="GET"):
            if calls is not None:
                calls.append((method, path))
            if path.startswith("/p20/jobs?"):
                return {"jobs": jobs}
            if "/claim" in path:
                return {"ok": claim_ok}
            return {"ok": True}
        return mock.patch.object(TF, "_desk_call", side_effect=fake)

    def test_collected_order_is_staged_atomically_with_provenance(self):
        with self._wire([_job()]):
            got = TF.collect_fleet_orders()
        self.assertEqual(got, 1)
        dest = os.path.join(self.qdir, "probe-order.tjob.json")
        self.assertTrue(os.path.exists(dest))
        with open(dest, encoding="utf-8") as f:
            spec = json.load(f)
        self.assertEqual(spec["fleet_order_id"], "j1")
        self.assertFalse(os.path.exists(dest + ".incoming"),
                         "the temp file must not survive a successful stage")

    def test_claim_refused_stages_nothing(self):
        with self._wire([_job()], claim_ok=False):
            got = TF.collect_fleet_orders()
        self.assertEqual(got, 0)
        self.assertEqual(os.listdir(self.qdir), [])

    def test_claim_happens_before_any_write(self):
        calls = []
        with self._wire([_job()], claim_ok=False, calls=calls):
            TF.collect_fleet_orders()
        self.assertTrue(any("/claim" in p for _m, p in calls))
        self.assertEqual(os.listdir(self.qdir), [],
                         "a refused claim must leave zero bytes behind")

    def test_malformed_record_is_skipped_not_fatal(self):
        with self._wire([{"id": "", "kind": "factory_order", "spec": {}}, _job("j2", "good")]):
            got = TF.collect_fleet_orders()
        self.assertEqual(got, 1)
        self.assertTrue(os.path.exists(os.path.join(self.qdir, "good.tjob.json")))

    def test_name_collision_disambiguates_with_the_desk_id(self):
        with open(os.path.join(self.qdir, "probe-order.tjob.json"), "w") as f:
            f.write("{}")
        with self._wire([_job(jid="abcdef99")]):
            got = TF.collect_fleet_orders()
        self.assertEqual(got, 1)
        self.assertTrue(os.path.exists(os.path.join(self.qdir, "probe-order-abcdef.tjob.json")))
        with open(os.path.join(self.qdir, "probe-order.tjob.json")) as f:
            self.assertEqual(f.read(), "{}", "the existing spec must never be clobbered")

    def test_non_factory_order_kinds_are_ignored(self):
        with self._wire([_job(kind="render")]):
            self.assertEqual(TF.collect_fleet_orders(), 0)

    def test_no_token_is_a_quiet_local_only_mode(self):
        with mock.patch.object(TF, "P20_TOKEN", ""):
            with self._wire([_job()]) as fake:
                self.assertEqual(TF.collect_fleet_orders(), 0)
                fake.assert_not_called()

    def test_desk_unreachable_is_fail_soft(self):
        def boom(path, method="GET"):
            raise OSError("no route")
        with mock.patch.object(TF, "_desk_call", side_effect=boom):
            self.assertEqual(TF.collect_fleet_orders(), 0)


class ReportFleetResult(unittest.TestCase):

    def setUp(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = mock.patch.object(TF, "DESK_REPORTS", os.path.join(d, "desk_reports"))   # never the factory's own state
        p.start()
        self.addCleanup(p.stop)

    def test_local_order_without_desk_id_is_a_noop(self):
        with mock.patch.object(TF, "_desk_call") as fake:
            TF.report_fleet_result({"name": "local"}, True)
            fake.assert_not_called()

    def test_done_and_failed_report_truthfully(self):
        calls = []
        with mock.patch.object(TF, "P20_TOKEN", "tok"), \
                mock.patch.object(TF, "_desk_call",
                                  side_effect=lambda p, method="GET": calls.append((method, p))):
            TF.report_fleet_result({"fleet_order_id": "j9"}, True)
            TF.report_fleet_result({"fleet_order_id": "j9"}, False, "control failed")
        self.assertEqual(calls[0][0], "POST")
        self.assertIn("status=done", calls[0][1])
        self.assertIn("status=failed", calls[1][1])
        self.assertIn("error=control%20failed", calls[1][1])

    def test_desk_outage_never_raises_out_of_the_retire_path(self):
        def boom(path, method="GET"):
            raise OSError("down")
        with mock.patch.object(TF, "P20_TOKEN", "tok"), \
                mock.patch.object(TF, "_desk_call", side_effect=boom):
            TF.report_fleet_result({"fleet_order_id": "j9"}, True)   # must not raise

    def test_a_refused_receipt_is_kept_waits_quietly_and_is_resent_once_the_desk_answers(self):
        """On refusal while the desk is down: keep the record, wait silently (no raise), and resend once the desk recovers."""
        def down(path, method="GET"):
            raise OSError("connection refused")
        logs = []
        with mock.patch.object(TF, "P20_TOKEN", "tok"), mock.patch.object(TF, "log", logs.append):
            with mock.patch.object(TF, "_desk_call", side_effect=down):
                TF.report_fleet_result({"fleet_order_id": "j9", "name": "o9"}, False, "boom")
                self.assertEqual(TF.resend_desk_reports(), [], "desk still down: WAIT")
            marker = os.path.join(TF.DESK_REPORTS, "j9.json")
            self.assertTrue(os.path.isfile(marker), "kept")
            self.assertFalse(any("RESENT" in x for x in logs))
            calls = []
            with mock.patch.object(TF, "_desk_call", side_effect=lambda p, method="GET": calls.append((method, p))):
                self.assertEqual(TF.resend_desk_reports(), ["j9"])
        self.assertEqual(len(calls), 1)
        self.assertIn("/p20/jobs/j9/result?status=failed", calls[0][1])
        self.assertIn("error=boom", calls[0][1])
        self.assertFalse(os.path.exists(marker), "delivered: marker gone")
        self.assertTrue(any("RESENT desk receipt for j9" in x for x in logs), logs)

    def test_a_desk_refusal_of_one_receipt_sets_it_aside_and_the_rest_still_go(self):
        import io
        import json
        import urllib.error
        os.makedirs(TF.DESK_REPORTS)
        for jid in ("a1", "b2"):
            with open(os.path.join(TF.DESK_REPORTS, jid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"jid": jid, "path": "/p20/jobs/%s/result?status=done" % jid, "name": jid}, f)

        def desk(path, method="GET"):
            if "/a1/" in path:
                raise urllib.error.HTTPError("u", 404, "no such step", {}, io.BytesIO(b""))
        with mock.patch.object(TF, "_desk_call", side_effect=desk), mock.patch.object(TF, "log"):
            self.assertEqual(TF.resend_desk_reports(), ["b2"])
        self.assertEqual(sorted(os.listdir(TF.DESK_REPORTS)), ["a1.json.refused"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
