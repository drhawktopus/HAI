"""`run_review_campaign` - the gated NI review-campaign kickoff (the campaign spine's front door).

Contract pinned: admin-only + sandbox-blocked (both locks), mode validation, UNC confinement,
work-file existence checked on the CONTAINER mount (a UNC path never resolves in this Linux
container), the canonical work-file name (the chain re-derives every later path from
work_dir+mode, so a campaign cannot arm on a name the merge receipt could never find),
order args matching the payloads' OWN interfaces, and two-step preview/confirm via
pending_actions. A work_file kickoff submits the review+merge STEPS order directly via
app.p20_order (self-cascading chain); a no-work_file kickoff stamps the campaign block
step="extract" on the extract spec so on_order_result drives the next phase. No test places
a real order. Existence tests use a real temp mount, never a blanket Path.exists mock."""
import pathlib
import tempfile
import unittest
from unittest import mock

import config
import pending_actions
import tools

ADMIN = "pintest-admin"
# The staging share is a setting, so the fixtures name their own UNC root and patch the tool's
# root to it: the confinement rule is tested against what the tool was configured with, never
# against a lab address written in the test.
SHARE = "\\\\testshare\\SSDShared\\library-triage"
WORK_DIR = SHARE + "\\pintest-campaign"
WORK_FILE = WORK_DIR + "\\corrective.work.json"

GOOD_EXTRACT = {"work_dir": WORK_DIR, "mode": "positive"}
GOOD_REVIEW = {"work_dir": WORK_DIR, "mode": "corrective", "work_file": WORK_FILE}

WORK_JSON = '{"items": [{"id": "it-1"}, {"id": "it-2"}, {"id": "ctrl-1", "control": true}]}'


class _CampaignCase(unittest.TestCase):
    """Admin patched, pending store cleared, a REAL temp dir standing in for the triage mount
    (with the fixture work file actually on disk), registrations captured."""

    def setUp(self):
        pending_actions._clear_for_tests()
        self._patch(config, "ADMIN_USERS", {ADMIN})
        self._patch(tools, "notes", mock.Mock())
        self._patch(tools, "_COMPUTE_ARG_ROOT", SHARE)

        self.mount = pathlib.Path(tempfile.mkdtemp())
        (self.mount / "pintest-campaign").mkdir()
        (self.mount / "pintest-campaign" / "corrective.work.json").write_text(
            WORK_JSON, encoding="utf-8")
        self._patch(config, "LIBRARY_TRIAGE_MOUNT", str(self.mount))

        self.registrations = []
        orig_register = pending_actions.register

        def fake_register(kind, preview, execute, user, **kw):
            result = orig_register(kind, preview, execute, user, **kw)
            self.registrations.append({"kind": kind, "preview": preview, "execute": execute,
                                       "user": user, "metadata": kw.get("metadata"),
                                       "token": result["action_token"]})
            return result

        self._patch(pending_actions, "register", fake_register)

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def handler(self, **over):
        user = over.pop("_user", ADMIN)
        args = dict(GOOD_EXTRACT)
        args.update(over)
        return tools._run_review_campaign(user, args)


class Gating(_CampaignCase):

    def test_non_admin_is_refused(self):
        out = self.handler(_user="pintest-guest")
        self.assertIn("Permission denied", out)
        self.assertEqual(len(self.registrations), 0)

    def test_admin_gate_table(self):
        self.assertFalse(tools._allowed("run_review_campaign", "pintest-guest"))
        self.assertTrue(tools._allowed("run_review_campaign", ADMIN))

    def test_sandbox_blocked(self):
        # An unattended overnight run must be MECHANICALLY unable to kick off a campaign.
        self.assertIn("run_review_campaign", tools.SANDBOX_BLOCKED_TOOLS)


class InputValidation(_CampaignCase):

    def test_invalid_mode_refused(self):
        out = self.handler(mode="sneaky")
        self.assertIn("invalid", out.lower())
        self.assertEqual(len(self.registrations), 0)

    def test_work_dir_outside_share_refused(self):
        out = self.handler(work_dir="\\\\10.0.0.204\\SSDShared\\mothership-build")
        self.assertIn("library-triage", out)
        self.assertEqual(len(self.registrations), 0)

    def test_work_file_outside_share_refused(self):
        out = self.handler(work_file="\\\\10.0.0.204\\SSDShared\\other\\x.work.json")
        self.assertIn("outside", out.lower())
        self.assertEqual(len(self.registrations), 0)

    def test_non_canonical_work_file_name_refused(self):
        # The chain derives all later paths from work_dir+mode; a custom-named work file
        # would arm a chain whose merge receipt can never find its out.
        out = self.handler(mode="corrective", work_file=WORK_DIR + "\\sample.work.json")
        self.assertIn("must be named corrective.work.json", out)
        self.assertEqual(len(self.registrations), 0)

    def test_missing_work_file_refused_via_real_mount(self):
        # The mount dir exists, the file does not - a REAL filesystem measurement, no mocks.
        # (positive.work.json is canonical for the default positive mode, so the name gate
        # passes and existence is what refuses.)
        out = self.handler(work_file=WORK_DIR + "\\positive.work.json")
        self.assertIn("does not exist", out.lower())
        self.assertEqual(len(self.registrations), 0)

    def test_absent_mount_is_failopen_with_warning(self):
        # "Could not check" must not render as "missing": preview proceeds, warning attached.
        self._patch(config, "LIBRARY_TRIAGE_MOUNT", str(self.mount / "nonexistent-mount"))
        self.handler(mode="corrective", work_file=WORK_FILE)
        self.assertEqual(len(self.registrations), 1)
        self.assertIn("could not verify", self.registrations[0]["preview"])


class PreviewPayload(_CampaignCase):

    def test_extract_argv_matches_payload_interface(self):
        # native_history_extract's argparse: --out-dir (required) + --mode. Positional args
        # were an earlier commission's invented dialect - this pin holds the real contract.
        # The extract spec now carries the campaign block so the chain follows its receipt.
        self.handler()
        reg = self.registrations[0]
        self.assertEqual(reg["metadata"]["payload"], "native_history_extract")
        import app
        with mock.patch.object(app, "p20_order", return_value={
                "ok": True, "routed_to": ["factory1"]}) as p20:
            reg["execute"]()
        self.assertEqual(p20.call_count, 1)
        spec = p20.call_args[0][0].spec
        self.assertEqual(spec["args"],
                         ["--out-dir", WORK_DIR, "--mode", "positive"])
        self.assertIn("campaign", spec)
        self.assertEqual(spec["campaign"]["step"], "extract")

    def test_review_argv_is_work_plus_derived_out(self):
        # native_history_review's argparse: positional work + out. The confirmed submission
        # is the review+merge steps order.
        self.handler(mode="corrective", work_file=WORK_FILE)
        reg = self.registrations[0]
        self.assertEqual(reg["metadata"]["payload"], "native_history_review")
        import app
        with mock.patch.object(app, "p20_order", return_value={
                "ok": True, "routed_to": ["factory1"]}) as p20:
            reg["execute"]()
        self.assertEqual(p20.call_count, 1)
        spec = p20.call_args[0][0].spec
        self.assertEqual(spec["args"],
                         [WORK_FILE, WORK_DIR + "\\corrective.review.json"])
        self.assertIn("steps", spec)
        steps = spec["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["name"], "shares")
        self.assertTrue(steps[0]["parallel"])
        # The deal's items are the fixture's non-control ids.
        self.assertEqual(steps[0]["items"], ["it-1", "it-2"])
        self.assertEqual(steps[1]["name"], "merge")
        self.assertEqual(steps[1]["after"], "shares")
        # Campaign block on merge step only, not on shares.
        self.assertNotIn("campaign", steps[0])
        self.assertIn("campaign", steps[1])
        self.assertEqual(steps[1]["campaign"]["step"], "merge")

    def test_preview_alone_submits_nothing(self):
        import app
        with mock.patch.object(app, "p20_order") as p20:
            self.handler()
            self.assertFalse(p20.called, "preview must not call p20_order")

    def test_preview_names_payload_and_args(self):
        self.handler()
        preview = self.registrations[0]["preview"]
        self.assertIn("native_history_extract", preview)
        self.assertIn("--out-dir", preview)

    def test_preview_mentions_self_cascading_chain(self):
        self.handler()
        preview = self.registrations[0]["preview"]
        self.assertIn("self-cascading", preview)
        self.assertIn("findings", preview)


class Confirm(_CampaignCase):

    def test_confirm_submits_exactly_one_order(self):
        import app
        with mock.patch.object(app, "p20_order", return_value={
                "ok": True, "routed_to": ["factory1"]}) as p20:
            self.handler()
            result = self.registrations[0]["execute"]()
        self.assertIn("campaign armed", result.lower())
        self.assertEqual(p20.call_count, 1)
        spec = p20.call_args[0][0].spec
        self.assertIn("native_history_extract.py", spec["payload"])
        self.assertEqual(spec["name"], "review-campaign-positive")
        self.assertIn("campaign", spec)
        self.assertEqual(spec["campaign"]["step"], "extract")

    def test_bogus_token_is_an_error(self):
        out = pending_actions.confirm("this-is-not-a-real-token", user=ADMIN)
        self.assertEqual(out["status"], "error")


class DispatchIntegration(_CampaignCase):
    """Through the REAL dispatch + pending machinery to app.p20_order - only the order sink is
    a spy. Proves the cross-seam wiring the unit pins mock away: the work_file path arms the
    steps chain, the no-work_file path stamps campaign step="extract" on the extract spec."""

    def setUp(self):
        super().setUp()
        self.orders = []
        import app
        self._patch(app, "p20_order", self._fake_p20)

    def _fake_p20(self, req):
        self.orders.append(req)
        spec = req.spec or {}
        return {"ok": True, "order": spec.get("name"), "routed_to": ["mothership"], "why": []}

    def test_extract_preview_confirm_end_to_end(self):
        result = tools.run_tool("run_review_campaign", dict(GOOD_EXTRACT), "", user=ADMIN)
        self.assertIn("action_token", result if isinstance(result, dict) else "")
        self.assertEqual(len(self.orders), 0, "preview must not submit")
        pending_actions.confirm(result["action_token"], user=ADMIN)
        self.assertEqual(len(self.orders), 1, "confirm must submit exactly one order")
        spec = self.orders[0].spec
        self.assertEqual(spec["payload"], "{FOUNDRY_ROOT}\\bin\\native_history_extract.py")
        self.assertEqual(spec["args"], ["--out-dir", WORK_DIR, "--mode", "positive"])
        self.assertIn("order.compute", list(self.orders[0].requires))
        self.assertIn("campaign", spec)
        self.assertEqual(spec["campaign"]["step"], "extract")
        self.assertEqual(spec["campaign"]["id"], "review-campaign-positive")
        self.assertEqual(spec["campaign"]["mode"], "positive")

    def test_review_preview_confirm_end_to_end(self):
        result = tools.run_tool("run_review_campaign", dict(GOOD_REVIEW), "", user=ADMIN)
        self.assertEqual(len(self.orders), 0, "preview must not submit")
        pending_actions.confirm(result["action_token"], user=ADMIN)
        self.assertEqual(len(self.orders), 1, "confirm must submit exactly one steps order")
        spec = self.orders[0].spec
        self.assertIn("native_history_review.py", spec["payload"])
        self.assertEqual(spec["args"], [WORK_FILE, WORK_DIR + "\\corrective.review.json"])
        self.assertEqual(spec["name"], "review-campaign-corrective-rev")
        self.assertIn("steps", spec)
        steps = spec["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["name"], "shares")
        self.assertTrue(steps[0]["parallel"])
        self.assertEqual(steps[1]["name"], "merge")
        self.assertEqual(steps[1]["after"], "shares")
        # A steps order carries parallel/items on the steps, never at the request level.
        self.assertFalse(self.orders[0].parallel)
        self.assertEqual(self.orders[0].items, [])
        # Campaign block on merge step only.
        self.assertNotIn("campaign", steps[0])
        self.assertIn("campaign", steps[1])
        self.assertEqual(steps[1]["campaign"]["step"], "merge")


if __name__ == "__main__":
    unittest.main()
