"""The media add-on (tools_ext_media + arr_tools) plugged into HAI's tools: both general pools keep the order they had
before the add-on was split out (the saved chat starts are built from that text), every tool is dispatched, and the
gates are as they were - changes admin-only, reads open."""
import json
import os

import arr_config
import unittest

import tools
import tools_ext_media as xm

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "media_pool_order.json")


def _names(pool):
    return [t["function"]["name"] for t in pool]


class TheAddOnKeepsEachPoolsOrder(unittest.TestCase):
    def test_both_pools_read_as_they_did_before_the_split(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            before = json.load(fh)
        for pool in ("BRAIN_TOOLS", "HEAVY_TOOLS"):
            # the fixture is the source's own order; other extensions and the appended host-admin tools are not in it
            known = set(before[pool])
            now = [n for n in _names(getattr(tools, pool)) if n in known]
            self.assertEqual(now, before[pool], pool)


class TheAddOnsToolsAreWiredIn(unittest.TestCase):
    def test_every_tool_is_offered_once_and_dispatched(self):
        names = [t["function"]["name"] for t in xm.TOOLS]
        self.assertEqual(len(names), 30)
        for pool in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            pn = _names(pool)
            for n in names:
                self.assertEqual(pn.count(n), 1, n)
        for n in names:
            self.assertIs(tools._EXT_HANDLERS[n], xm.HANDLERS[n])

    def test_changes_are_admin_only_and_reads_are_open(self):
        for n in xm.ADMIN:
            self.assertIn(n, tools._ADMIN_TOOLS)
            self.assertFalse(tools._allowed(n, "a-user-who-is-not-an-admin"), n)
        for n in xm.OPEN:
            self.assertNotIn(n, tools._ADMIN_TOOLS)
        self.assertEqual(len(xm.ADMIN) + len(xm.OPEN), 30)
        self.assertIn("recyclarr_sync", xm.ADMIN)
        self.assertIn("check_downloads", xm.OPEN)

    def test_the_write_tools_go_through_the_semantic_check(self):
        """Each write tool's preview is handed to tools._finish_pending with the request's words (the Tier-2 check)."""
        from unittest import mock
        import arr_tools
        for tool, fn in (("qbit_remove", "qbit_remove_preview"), ("qbit_add", "qbit_add_preview"),
                         ("qbit_action", "qbit_action_preview"), ("sonarr_grab", "sonarr_grab_preview"),
                         ("sonarr_manual_import", "sonarr_manual_import_preview"),
                         ("sonarr_search", "sonarr_search_preview")):
            seen = []
            with mock.patch.object(arr_tools, fn, lambda *a, **k: {"status": "pending"}), \
                    mock.patch.object(tools, "_finish_pending", lambda result, words: seen.append((result, words)) or "checked"):
                self.assertEqual(xm.HANDLERS[tool]("admin", {}, "the words"), "checked", tool)
            self.assertEqual(seen, [({"status": "pending"}, "the words")], tool)

    def test_a_handler_that_takes_the_request_words_gets_them(self):
        seen = []
        self.assertEqual(tools._ext_call(lambda u, a, t: seen.append((u, a, t)) or "ok", "u", {"x": 1}, "words"), "ok")
        self.assertEqual(seen, [("u", {"x": 1}, "words")])
        self.assertEqual(tools._ext_call(lambda u, a: "two", "u", {}, "words"), "two")


class TheAddOnsServicesRisksAndCredentials(unittest.TestCase):
    def test_check_services_reads_as_it_always_did(self):
        import infra
        self.assertEqual(list(infra.SERVICES)[:5], ["Sonarr", "Radarr", "Lidarr", "Prowlarr", "qBittorrent"])
        self.assertEqual(tools._SERVICES["function"]["description"],
                         "Check whether the homelab services (Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent, Plex, "
                         "Tautulli) are up. Use for 'is everything running?', 'is Sonarr down?'.")

    def test_the_add_ons_risk_reasons_are_added(self):
        import pending_actions
        level, why = pending_actions.assess_risk("qbit.remove", True, {"action": "remove", "hashes": "a"})
        self.assertEqual(level, "MED")
        self.assertIn("removes torrents from qBittorrent", why)
        level, why = pending_actions.assess_risk("sonarr.manual_import", False, {"count": 1, "import_mode": "move"})
        self.assertEqual((level, why), ("MED", ["moves files into the library"]))

    def test_loading_the_add_on_again_adds_no_second_copy(self):
        import pending_actions
        before = list(pending_actions.RISK_RULES)
        self.assertEqual(before.count(xm._risk), 1)
        tools._add_risk_rules(xm)
        tools._add_risk_rules(xm)
        self.assertEqual(pending_actions.RISK_RULES.count(xm._risk), 1)
        self.assertEqual(len(pending_actions.RISK_RULES), len(before))

    def test_a_head_without_the_add_on_has_only_its_own_rules(self):
        from unittest import mock
        import pending_actions
        with mock.patch.object(pending_actions, "RISK_RULES", []):
            self.assertEqual(pending_actions.assess_risk("sonarr.manual_import", False, {"import_mode": "move"}),
                             ("LOW", []))


class TheBrokersRecyclarrRoute(unittest.TestCase):
    def test_the_route_comes_from_the_add_on_and_the_request_cannot_change_the_run(self):
        """hostops_sidecar loads hostops_sidecar_ext_media's route; the request picks dry-run or not, nothing else."""
        import hostops_sidecar
        route = hostops_sidecar.ONEOFF_ROUTES["/recyclarr/sync"]
        dry = route({"preview": True, "image": "attacker/image", "cmd": ["sh", "-c", "id"]})
        real = route({"preview": False})
        self.assertEqual(dry["cmd"], ["sync", "--preview"])
        self.assertEqual(real["cmd"], ["sync"])
        self.assertEqual(dry["image"], "ghcr.io/recyclarr/recyclarr:latest")
        self.assertEqual((dry["result"], real["result"]), ({"dry_run": True}, {"dry_run": False}))
        self.assertEqual(dry["env"], ["TZ=" + (os.environ.get("RECYCLARR_TZ") or os.environ.get("TZ") or "UTC")],
                 "recyclarr runs in the lab's zone: RECYCLARR_TZ, else the container's TZ, else UTC")
        self.assertEqual((dry["binds"], dry["user"]), (["/opt/arrstack/recyclarr:/config"], "1000:1000"))


class RecyclarrSyncTests(unittest.TestCase):
    """The add-on's Recyclarr sync: a dry-run by default, run by the hostops broker once confirmed."""
    def setUp(self):
        import pending_actions
        pending_actions._clear_for_tests()

    def tearDown(self):
        import pending_actions
        pending_actions._clear_for_tests()

    def test_recyclarr_default_is_dry_run_and_not_destructive(self):
        from unittest.mock import patch
        import arr_tools
        import config
        import pending_actions
        with patch("hostops._request", return_value={"dry_run": True, "exit_code": 0, "output": "would change"}) as req:
            pv = arr_tools.recyclarr_sync_preview(user="Alex")   # default apply=False
            self.assertEqual(pv["status"], "pending")
            self.assertFalse(pv["destructive"])                   # dry-run is harmless
            self.assertIn("DRY-RUN", pv["preview"])
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(req.call_args.kwargs.get("json_body"), {"preview": True})   # --preview path
        self.assertEqual(req.call_args.kwargs.get("timeout"), arr_config.RECYCLARR_SYNC_TIMEOUT)

    def test_recyclarr_apply_is_destructive_with_warning(self):
        from unittest.mock import patch
        import arr_tools
        import pending_actions
        pv = arr_tools.recyclarr_sync_preview(apply=True, user="Alex")
        self.assertTrue(pv["destructive"])                        # real apply gets the risk banner
        self.assertIn("reset_unmatched_scores", pv["preview"])    # warns about wiping hand-tuned scores
        self.assertIn("4K WEB", pv["preview"])
        with patch("hostops._request", return_value={"dry_run": False, "exit_code": 0}) as req:
            pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(req.call_args.kwargs.get("json_body"), {"preview": False})   # real sync path


if __name__ == "__main__":
    unittest.main()
