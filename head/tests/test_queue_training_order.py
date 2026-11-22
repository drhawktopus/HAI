"""Tests that `queue_training` submits orders rather than directly launching the foundry.

This behavior is critical because direct launching bypasses the order system,
leaving no queue, lane claims, control slips, retirement, or archive records.
Behavior tests are used instead of source-text assertions to prevent regression
if implementation details change while the contract holds."""
import unittest
from unittest import mock

import config
import scheduler
import tools

ADMIN = "pintest-admin"

GOOD = {
    "name": "testsubject_flux2_v1",
    "launcher": r"F:\ai-toolkit\run_testsubject.bat",
    "log": r"F:\flux2-train\testsubject.log",
    "output_dir": r"F:\flux2-train\output\testsubject",
}


class _ToolCase(unittest.TestCase):
    """Admin identity patched, the desk faked, and the LAUNCH PATH ARMED TO EXPLODE.

    The mothership fakes are the positive control for this whole file: if the tool ever reaches for
    them again, every test in the class fails loudly instead of one narrow assertion going quiet.
    """

    def setUp(self):
        self.submitted = []

        def fake_desk(req):
            self.submitted.append(req)
            spec = req.spec or {}
            host = (list(req.only) or ["mothership"])[0]
            return {"ok": True, "order": spec.get("name"),
                    "routed_to": [host], "why": [],
                    "steps": [{"host": host, "id": "job-1",
                               "order": "%s_%s" % (spec.get("name"), host)}]}

        self.desk = fake_desk
        self._patch(config, "ADMIN_USERS", {ADMIN})
        self._patch_app()

        import lead_ssh
        for gone in ("write_foundry_spec", "start_foundry", "trainer_running"):
            if hasattr(lead_ssh, gone):
                self._patch(lead_ssh, gone, self._forbidden(gone))

    def _forbidden(self, what):
        def boom(*a, **k):
            raise AssertionError(
                "queue_training called mothership.%s — that is the ORDER-SYSTEM BYPASS this "
                "rewiring removed. Submit an order; never launch a process." % what)
        return boom

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def _patch_app(self):
        import app
        self._patch(app, "p20_order", lambda req: self.desk(req))

    def run_tool(self, **over):
        args = dict(GOOD)
        args.update(over)
        return tools._queue_training(over.pop("_user", ADMIN), args)


class TheBypassIsGone(_ToolCase):

    def test_never_launches_a_foundry_process(self):
        """THE ONE THAT MATTERS. Repointing the dead constants at the green tree would fix the
        crash and leave the bypass — this fails if the launch path is ever called again."""
        out = self.run_tool()
        self.assertEqual(len(self.submitted), 1, "no order was submitted: %s" % out)

    def test_submits_through_the_fleet_desk(self):
        self.run_tool()
        self.assertEqual(len(self.submitted), 1)

    def test_states_the_train_capability(self):
        self.run_tool()
        self.assertEqual(list(self.submitted[0].requires), ["train"])


class TheOrderLandsInTheRightBand(_ToolCase):

    def test_the_spec_is_self_describing(self):
        """Verify that the spec's 'mode' explicitly dictates the scheduling band, independent
        of runtime 'requires' hints. Without an explicit mode, specs fall back to the
        default 'caption' band upon requeue or archival, risking resource mismatch."""
        self.run_tool()
        spec = self.submitted[0].spec
        self.assertEqual(scheduler.kind_for_order(spec, []), "train")
        self.assertEqual(spec.get("mode"), "train")

    def test_operator_fields_ride_verbatim(self):
        self.run_tool(total_steps=3000, preview_every=250)
        spec = self.submitted[0].spec
        self.assertEqual(spec["launcher"], GOOD["launcher"])
        self.assertEqual(spec["log"], GOOD["log"])
        self.assertEqual(spec["output_dir"], GOOD["output_dir"])
        self.assertEqual(spec["total_steps"], 3000)
        self.assertEqual(spec["preview_every"], 250)

    def test_milestones_still_dm(self):
        self.run_tool()
        self.assertEqual(self.submitted[0].spec["initiator"], "direct")


class BusyIsAQueuePositionNotARefusal(_ToolCase):

    def test_busy_card_still_places_the_order(self):
        """Busy means wait (queue), not refuse. Only the order itself may consume the lane."""
        import p20_queue
        self._patch(p20_queue, "gpu_status", lambda *a, **k: {"state": "training"})
        out = self.run_tool()
        self.assertEqual(len(self.submitted), 1,
                         "a busy card refused the order instead of queueing it: %s" % out)


class ManualControlIsAHardPin(_ToolCase):

    def test_no_host_leaves_placement_to_starfleet(self):
        self.run_tool()
        self.assertEqual(list(self.submitted[0].only), [])

    def test_named_host_pins_hard(self):
        self.run_tool(host="mothership")
        self.assertEqual(list(self.submitted[0].only), ["mothership"])

    def test_host_is_case_and_space_tolerant(self):
        self.run_tool(host="  MotherShip ")
        self.assertEqual(list(self.submitted[0].only), ["mothership"])

    def test_unknown_host_refuses_and_submits_nothing(self):
        """Checked against scheduler.CARDS, the component that owns the host table — never a list
        retyped here: a retyped list turns a rename into a silent misplacement."""
        out = self.run_tool(host="enterprise")
        self.assertEqual(self.submitted, [], "an unknown host still placed an order")
        self.assertIn("enterprise", out)

    def test_every_real_factory_is_accepted(self):
        for host in sorted(scheduler.CARDS):
            with self.subTest(host=host):
                self.submitted.clear()
                self.run_tool(host=host)
                self.assertEqual(list(self.submitted[0].only), [host])


class RefusalsAreHonest(_ToolCase):

    def test_non_admin_refused_and_submits_nothing(self):
        out = self.run_tool(_user="somebody-else")
        self.assertEqual(self.submitted, [])
        self.assertIn("admin", out.lower())

    def test_missing_required_fields_submits_nothing(self):
        for missing in ("launcher", "log", "output_dir"):
            with self.subTest(missing=missing):
                self.submitted.clear()
                self.run_tool(**{missing: ""})
                self.assertEqual(self.submitted, [])

    def test_desk_refusal_is_reported_not_raised(self):
        """Tool must return a failure string on refusal, not crash the /query endpoint."""
        self.desk = lambda req: {"ok": False, "error": "no factory can build it",
                                 "why": ["nobody declares train"], "rejected": {}}
        out = self.run_tool()
        self.assertIsInstance(out, str)
        self.assertIn("REFUSED", out)
        self.assertIn("nothing", out.lower())


if __name__ == "__main__":
    unittest.main()
