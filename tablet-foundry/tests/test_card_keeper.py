"""Reboot rigby when a held card is missing from the driver, provided both lanes are idle, an order is not waiting on it, and no prior attempt has been made for this drop."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rigby_card_keeper as K  # noqa: E402

NOW = 1760700000.0
SMI_BOTH = "GPU 0: NVIDIA GeForce GTX 1060 6GB (UUID: GPU-a)\nGPU 1: NVIDIA GeForce GT 730 (UUID: GPU-b)\n"
SMI_730 = "GPU 0: NVIDIA GeForce GT 730 (UUID: GPU-b)\n"


def marker(age=10.0, cards=("gtx1060",), at=NOW):
    return {"order": "r1060", "cards": list(cards), "period_s": 30, "since": at - 600, "updated": at - age}


class Decide(unittest.TestCase):
    def setUp(self):
        self._expected = K.EXPECTED
        K.EXPECTED = ("gtx1060", "gt730")        # the CARD_KEEPER_CARDS setting (rigby's cards), as a fixture

    def tearDown(self):
        K.EXPECTED = self._expected

    def test_unset_state_means_no_action_and_says_so(self):
        import unittest.mock as mock
        with mock.patch.object(K, "STATE", ""), mock.patch.object(K, "subprocess") as sp, mock.patch("builtins.print") as out:
            self.assertEqual(K.main([]), 2)
        sp.run.assert_not_called()
        self.assertIn("CARD_KEEPER_STATE", " ".join(str(c) for c in out.call_args_list))

    def test_the_service_unit_loads_the_host_settings_file(self):
        self.assertIn("EnvironmentFile=-%s\n" % K.ENVFILE, K.UNITS["rigby-card-keeper.service"])

    def test_a_waiting_order_a_missing_card_and_idle_lanes_reboot_once(self):
        action, rec, why = K.decide(marker(), {}, SMI_730, [], "boot-1", NOW)
        self.assertEqual(action, "reboot", why)
        self.assertEqual((rec["rebooted_from_boot"], rec["cards"], rec["order"]), ("boot-1", ["gtx1060"], "r1060"))

    def test_a_missing_card_reboots_with_no_order_waiting(self):
        action, rec, why = K.decide({}, {}, SMI_730, [], "boot-1", NOW)
        self.assertEqual((action, rec["cards"], rec["order"]), ("reboot", ["gtx1060"], None), why)
        self.assertIn("no order waiting", why)
        self.assertEqual(K.decide(marker(age=91), {}, SMI_730, [], "boot-1", NOW)[0], "reboot",
                         "a stale marker is no order - the missing card is still a drop")
        self.assertEqual(K.decide(marker(age=89), {}, SMI_730, [], "boot-1", NOW)[0], "reboot", "inside 3x")

    def test_every_card_visible_and_no_order_is_nothing(self):
        action, rec, why = K.decide({}, {}, SMI_BOTH, [], "boot-1", NOW)
        self.assertEqual((action, rec), ("none", None), why)

    def test_a_dead_driver_listing_nothing_is_a_drop_of_both_and_still_once(self):
        action, rec, _why = K.decide({}, {}, "", [], "boot-1", NOW)
        self.assertEqual((action, rec["cards"]), ("reboot", ["gtx1060", "gt730"]))
        self.assertEqual(K.decide({}, rec, "", [], "boot-2", NOW + 300)[0], "give_up", "no reboot loop on a dead driver")

    def test_a_no_order_drop_still_never_reboots_under_running_work(self):
        self.assertEqual(K.decide({}, {}, SMI_730, ["CURRENT_ORDER.rigby.json"], "boot-1", NOW)[0], "none")

    def test_a_no_order_drop_ends_when_the_card_is_back(self):
        rebooted = {"rebooted_at": NOW - 300, "rebooted_from_boot": "boot-1", "order": None, "cards": ["gtx1060"]}
        action, rec, _why = K.decide({}, rebooted, SMI_BOTH, [], "boot-2", NOW)
        self.assertEqual((action, sorted(rec)), ("none", ["last_ok", "last_reboot"]))

    def test_never_under_running_work(self):
        action, _rec, why = K.decide(marker(), {}, SMI_730, ["CURRENT_ORDER.json"], "boot-1", NOW)
        self.assertEqual(action, "none")
        self.assertIn("never reboot under running work", why)

    def test_a_visible_card_is_left_to_tolkien(self):
        self.assertEqual(K.decide(marker(), {}, SMI_BOTH, [], "boot-1", NOW)[0], "none")

    def test_still_missing_in_the_boot_after_its_own_reboot_gives_up_and_stays_given_up(self):
        rebooted = {"rebooted_at": NOW - 300, "rebooted_from_boot": "boot-1", "order": "r1060", "cards": ["gtx1060"]}
        action, rec, why = K.decide(marker(), rebooted, SMI_730, [], "boot-2", NOW)
        self.assertEqual(action, "give_up", why)
        self.assertEqual((rec["gave_up"], rec["gave_up_why"]), (NOW, "still missing in the boot after the keeper's reboot"))
        self.assertEqual(K.decide(marker(), rec, SMI_730, [], "boot-2", NOW + 120)[0], "none", "no reboot loop")

    def test_a_reboot_that_never_happened_gives_up_instead_of_waiting_forever(self):
        rebooted = {"rebooted_at": NOW - 120, "rebooted_from_boot": "boot-1", "order": "r1060", "cards": ["gtx1060"]}
        action, rec, _why = K.decide(marker(), rebooted, SMI_730, [], "boot-1", NOW)
        self.assertEqual(action, "give_up")
        self.assertIn("never rebooted", rec["gave_up_why"])

    def test_giving_up_is_a_report_and_never_waits_for_idle_lanes(self):
        rebooted = {"rebooted_at": NOW - 300, "rebooted_from_boot": "boot-1", "order": "r1060", "cards": ["gtx1060"]}
        self.assertEqual(K.decide(marker(), rebooted, SMI_730, ["CURRENT_ORDER.json"], "boot-2", NOW)[0], "give_up")

    def test_every_run_stamps_a_heartbeat_and_a_decision_replaces_only_the_decision_fields(self):
        gave = {"gave_up": NOW - 50, "rebooted_from_boot": "boot-1", "checked": NOW - 120}
        kept = K.record(gave, None, NOW, "none", "waiting for a human")
        self.assertEqual((kept["gave_up"], kept["checked"], kept["period_s"]), (NOW - 50, NOW, K.PERIOD_S))
        over = K.record(gave, {"last_ok": NOW, "last_reboot": NOW - 400}, NOW, "none", "the drop is over")
        self.assertNotIn("gave_up", over, "a decision replaces the old decision fields")
        self.assertEqual(over["checked"], NOW)

    def test_the_units_it_writes_run_it_at_the_period_it_declares(self):
        """The heartbeat's period_s is how Tolkien tells a live keeper from a dead one - so the timer the keeper writes
        for itself must run at exactly that cadence, and run this file as root via the service."""
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                K.write_units(d)
            with open(os.path.join(d, "rigby-card-keeper.timer"), encoding="utf-8") as f:
                timer = f.read().splitlines()
            with open(os.path.join(d, "rigby-card-keeper.service"), encoding="utf-8") as f:
                service = f.read().splitlines()
        self.assertEqual([ln for ln in timer if ln.startswith("OnUnitActiveSec=")], ["OnUnitActiveSec=%ds" % K.PERIOD_S])
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("ExecStart=/usr/bin/python3 /usr/local/bin/rigby_card_keeper.py", service)

    def test_a_card_seen_again_ends_the_drop_so_the_next_drop_may_reboot(self):
        rebooted = {"rebooted_at": NOW - 300, "rebooted_from_boot": "boot-1", "gave_up": NOW - 100}
        action, rec, _why = K.decide(marker(), rebooted, SMI_BOTH, [], "boot-2", NOW)
        self.assertEqual((action, sorted(rec)), ("none", ["last_ok", "last_reboot"]))
        self.assertEqual(K.decide(marker(at=NOW + 3600), rec, SMI_730, [], "boot-2", NOW + 3600)[0], "reboot",
                         "a fresh drop")


if __name__ == "__main__":
    unittest.main()
