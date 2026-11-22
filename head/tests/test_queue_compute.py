"""Tests for `queue_compute` - the GPU-fleet general compute counter (#12 G4a).

Behavior tests against the tool contract: whitelisted payloads only, confined UNC args,
size-reserve-registered parallel deals, both-locks gating, and submission by internal call
to the desk. No test may place a real order - the desk is faked in setUp."""
import unittest
from unittest import mock

import config
import scheduler
import tools

ADMIN = "pintest-admin"
# A staging share is a setting, so the fixtures name their own: the confinement rule is tested
# against the root the tool was configured with, never against a lab address.
SHARE = r"\\testshare\SSDShared\library-triage"
GOOD = {"name": "pintest_compute_v1", "payload": "triage_code",
        "args": [SHARE + r"\pintest.work.json"]}


class _ToolCase(unittest.TestCase):
    """Admin identity patched, the desk faked, notes silenced - a captured FactoryOrderReq per call."""

    def setUp(self):
        self.submitted = []

        def fake_desk(req):
            self.submitted.append(req)
            spec = req.spec or {}
            host = (list(req.only) or ["mothership"])[0]
            return {"ok": True, "order": spec.get("name"), "routed_to": [host], "why": []}

        self.desk = fake_desk
        self._patch(config, "ADMIN_USERS", {ADMIN})
        self._patch(tools, "notes", mock.Mock())
        self._patch(tools, "_COMPUTE_ARG_ROOT", SHARE)
        import app
        self._patch(app, "p20_order", lambda req: self.desk(req))

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def run_tool(self, **over):
        user = over.pop("_user", ADMIN)
        args = dict(GOOD)
        args.update(over)
        return tools._queue_compute(user, args)


class Gating(_ToolCase):

    def test_non_admin_is_refused(self):
        out = self.run_tool(_user="pintest-guest")
        self.assertIn("admin-only", out)
        self.assertEqual(self.submitted, [])

    def test_sandbox_blocked(self):
        """An unattended overnight run must be MECHANICALLY unable to commit GPU compute."""
        self.assertIn("queue_compute", tools.SANDBOX_BLOCKED_TOOLS)

    def test_admin_gate_table(self):
        self.assertFalse(tools._allowed("queue_compute", "pintest-guest"))
        self.assertTrue(tools._allowed("queue_compute", ADMIN))


class TheWhitelist(_ToolCase):

    def test_unknown_payload_refused_listing_options(self):
        out = self.run_tool(payload="rm_rf_everything")
        self.assertIn("not in the whitelist", out)
        self.assertIn("triage_code", out)
        self.assertEqual(self.submitted, [])

    def test_every_registry_entry_is_complete(self):
        for name, entry in tools.COMPUTE_PAYLOADS.items():
            self.assertIn("order.compute", entry["requires"], name)
            self.assertIn(entry["load"], ("gpu", "cpu"), name)


class ArgConfinement(_ToolCase):

    def test_unc_outside_library_triage_refused(self):
        out = self.run_tool(args=[r"\\testshare\SSDShared\mothership-build\p20_foundry.py"])
        self.assertIn("outside the library-triage share", out)
        self.assertEqual(self.submitted, [])

    def test_flags_and_local_tokens_pass(self):
        out = self.run_tool(args=["--merge", SHARE + r"\x.json"])
        self.assertIn("ORDER placed", out)
        self.assertEqual(len(self.submitted), 1)

    def test_an_unc_arg_is_refused_when_no_share_is_configured(self):
        """Empty is 'not configured', not 'anything goes': with no staging share, an UNC arg is refused."""
        self._patch(tools, "_COMPUTE_ARG_ROOT", "")
        out = self.run_tool(args=[r"\\somewhere\SSDShared\work.json"])
        self.assertIn("no library-triage share is configured", out)
        self.assertEqual(self.submitted, [])


class HostPin(_ToolCase):

    def test_unknown_host_refused_listing_fleet(self):
        out = self.run_tool(host="averys-laptop")
        self.assertIn("don't know a factory", out)
        self.assertEqual(self.submitted, [])

    def test_fellowship_is_a_legal_pin(self):
        """Compute can place on non-card factories; the pin validates the UNION, not CARDS alone."""
        self.run_tool(payload="native_history_extract", host="fellowship")
        self.assertEqual(list(self.submitted[0].only), ["fellowship"])

    def test_no_pin_means_starfleet_places(self):
        self.run_tool()
        self.assertEqual(list(self.submitted[0].only), [])


class ParallelDeals(_ToolCase):

    def test_items_without_parallel_refused(self):
        out = self.run_tool(items=["a", "b"])
        self.assertIn("parallel", out)
        self.assertEqual(self.submitted, [])

    def test_unregistered_payload_refused_for_parallel_items(self):
        """Unregistered payloads lack size reserves; parallel submission must be refused.
        Asserts against scheduler.SIZE_RESERVES to keep the test and config in sync."""
        self.assertFalse(any(k in "native_history_findings" for k in scheduler.SIZE_RESERVES))
        out = self.run_tool(payload="native_history_findings", parallel=True, items=["a"])
        self.assertIn("size-reserve", out)
        self.assertEqual(self.submitted, [])

    def test_registered_payload_deals_parallel(self):
        self.assertTrue(any(k in "native_history_review" for k in scheduler.SIZE_RESERVES))
        self.run_tool(payload="native_history_review", parallel=True, items=["a", "b"])
        req = self.submitted[0]
        self.assertTrue(req.parallel)
        self.assertEqual(list(req.items), ["a", "b"])


class TheOrderItself(_ToolCase):

    def test_spec_shape(self):
        self.run_tool()
        req = self.submitted[0]
        spec = req.spec
        self.assertEqual(spec["mode"], "compute")
        self.assertEqual(spec["load"], "gpu")
        self.assertEqual(spec["payload"], "{FOUNDRY_ROOT}\\bin\\triage_code.py")
        self.assertEqual(spec["notify"], "failures")
        self.assertEqual(list(req.requires), ["order.compute", "model." + config.HEAVY_MODEL])

    def test_cpu_payload_takes_the_cpu_band(self):
        """load rides the registry, and the desk's own kind derivation puts cpu compute in the
        small band - asserted through scheduler.kind_for_order, never a copied band table."""
        self.run_tool(payload="native_history_findings")
        spec = self.submitted[0].spec
        self.assertEqual(spec["load"], "cpu")
        self.assertEqual(scheduler.kind_for_order(spec, list(self.submitted[0].requires)), "caption")

    def test_desk_refusal_is_reported_not_swallowed(self):
        def refusing_desk(req):
            self.submitted.append(req)
            return {"ok": False, "error": "no factory declares order.compute", "why": ["x"]}
        self.desk = refusing_desk
        out = self.run_tool()
        self.assertIn("REFUSED and NOTHING is queued", out)


class TheWire(_ToolCase):
    """An unwired tool must fail identically to a nonexistent one."""

    def test_schema_is_offered(self):
        names = {t["function"]["name"] for t in tools.BRAIN_TOOLS}
        self.assertIn("queue_compute", names)
        names = {t["function"]["name"] for t in tools.HEAVY_TOOLS}
        self.assertIn("queue_compute", names)

    def test_dispatch_reaches_the_handler(self):
        sentinel = mock.Mock(return_value="ok-from-handler")
        self._patch(tools, "_queue_compute", sentinel)
        out = tools.run_tool("queue_compute", dict(GOOD), "", user=ADMIN)
        self.assertTrue(sentinel.called, "dispatch never reached _queue_compute: %r" % (out,))


if __name__ == "__main__":
    unittest.main()
