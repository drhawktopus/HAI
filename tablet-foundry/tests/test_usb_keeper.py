"""USB link keeper: restores a node whose gadget came up without rndis. Action requires a higher bar than reporting: two confirming passes, per-node cooldown, and runner idle at the moment of acting.

Status shapes match /fellowship/status: 'gandalf' (link down) and 'frodo' (link up)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_control as tc
import tablet_foundry as _tf  # noqa: E402

_WIRE = mock.patch.object(_tf, "WIRE_PREFIX", "192.0.2.")    # the wire subnet is a setting; these tests use a documentation one


def setUpModule():
    _WIRE.start()


def tearDownModule():
    _WIRE.stop()


DOWN = {"node": "gandalf", "adb": "shell", "android": 12, "usb_config": "none", "rndis0": "-", "rndis0_state": "-", "octet": 16}
UP = {"node": "frodo", "adb": "root", "android": 11, "usb_config": "rndis,adb", "rndis0": "192.0.2.11/24", "rndis0_state": "up", "octet": 11}
A11_UP = {"node": "faramir", "adb": "root", "android": 11, "usb_config": "adb", "rndis0": "192.0.2.21/24", "rndis0_state": "up", "octet": 21}
# address on a DOWN interface carries nothing (boromir, same night: carrier 0)
ADDR_BUT_DOWN = {"node": "boromir", "adb": "root", "android": 11, "usb_config": "adb", "rndis0": "192.0.2.20/24", "rndis0_state": "down", "octet": 20}
NO_ADB = {"node": "gandalf", "adb": "unreachable", "wifi": "10.0.0.71"}


class LinkVerdict(unittest.TestCase):
    def test_real_shapes(self):
        self.assertIs(tc.usb_link_down(DOWN), True)
        self.assertIs(tc.usb_link_down(UP), False)
        self.assertIsNone(tc.usb_link_down(NO_ADB))

    def test_the_interface_decides_not_the_usb_config_prop(self):
        self.assertIs(tc.usb_link_down(A11_UP), False)
        self.assertIs(tc.usb_link_down(ADDR_BUT_DOWN), True)

    def test_gadget_up_but_wire_address_missing_is_down(self):
        self.assertIs(tc.usb_link_down(dict(UP, rndis0="-")), True)
        self.assertIs(tc.usb_link_down(dict(UP, rndis0="192.0.2.12/24")), True)   # another node's address

    def test_an_up_interface_with_offload_on_is_down(self):
        self.assertIs(tc.usb_link_down(dict(UP, offload="on")), True)
        self.assertIs(tc.usb_link_down(dict(UP, offload="off")), False)

    def test_unknown_offload_leaves_the_interface_verdict_standing(self):
        self.assertIs(tc.usb_link_down(dict(UP, offload="?")), False)
        self.assertIs(tc.usb_link_down(dict(DOWN, offload="?")), True)
        self.assertIsNone(tc.usb_link_down(dict(NO_ADB, offload="on")))


class KeeperPasses(unittest.TestCase):
    def setUp(self):
        tc._KEEP["down"].clear()
        tc._KEEP["acted"].clear()
        tc._KEEP["no_host"].clear()
        self.restored = []
        self.readings = {"gandalf": DOWN, "frodo": UP}
        self.idle = True

    def run_pass(self, t):
        return tc.keep_usb_links(names=["gandalf", "frodo"], now_t=t,
                                 status_fn=lambda n: self.readings[n],
                                 restore_fn=lambda n: (self.restored.append(n) or {"node": n, "ok": True}),
                                 idle=lambda: self.idle)

    def test_confirms_then_restores_once_then_cools_down(self):
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(0)], [("gandalf", "seen")])
        self.assertEqual(self.restored, [])
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(300)], [("gandalf", "restored")])
        self.assertEqual(self.restored, ["gandalf"])
        self.run_pass(600)                                   # still down: re-confirming, not acting
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(900)], [("gandalf", "cooldown")])
        self.assertEqual(self.restored, ["gandalf"])
        self.run_pass(300 + tc.USB_KEEP_COOLDOWN_S)
        self.assertEqual(self.restored, ["gandalf", "gandalf"])

    def test_never_acts_while_the_runner_is_busy(self):
        self.run_pass(0)
        self.idle = False
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(300)], [("gandalf", "deferred-busy")])
        self.assertEqual(self.restored, [])

    def test_cannot_tell_neither_counts_nor_clears(self):
        self.run_pass(0)                                     # seen x1
        self.readings["gandalf"] = NO_ADB
        self.assertEqual(self.run_pass(300), [])             # no count, no reset
        self.readings["gandalf"] = DOWN
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(600)], [("gandalf", "restored")])

    def test_link_back_up_clears_the_count(self):
        self.run_pass(0)
        self.readings["gandalf"] = dict(UP, node="gandalf", rndis0="192.0.2.16/24", octet=16)
        self.run_pass(300)
        self.readings["gandalf"] = DOWN
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(600)], [("gandalf", "seen")])

    def test_offload_on_is_confirmed_then_restored(self):
        self.readings["gandalf"] = dict(UP, node="gandalf", rndis0="192.0.2.16/24", octet=16, offload="on")
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(0)], [("gandalf", "seen")])
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(300)], [("gandalf", "restored")])

    def test_healthy_fleet_does_nothing(self):
        self.readings["gandalf"] = dict(UP, node="gandalf", rndis0="192.0.2.16/24", octet=16)
        self.assertEqual(self.run_pass(0) + self.run_pass(300), [])
        self.assertEqual(self.restored, [])

    def test_failed_restore_is_reported_not_retried_inside_cooldown(self):
        self.readings = {"gandalf": DOWN}
        fails = []
        r = lambda: tc.keep_usb_links(names=["gandalf"], now_t=self.t, status_fn=lambda n: DOWN,
                                      restore_fn=lambda n: (fails.append(n) or {"node": n, "ok": False, "why": "x"}),
                                      idle=lambda: True)
        self.t = 0; r()
        self.t = 300
        self.assertEqual([(n, a) for n, a, _ in r()], [("gandalf", "restore-failed")])
        self.t = 600; r(); self.t = 900
        self.assertEqual(fails, ["gandalf"])


NO_HOST = {"node": "legolas", "adb": "root", "android": 11, "usb_config": "rndis,adb", "rndis0": "192.0.2.13/24",
           "rndis0_state": "down", "octet": 13, "offload": "off", "udc_state": "not attached"}
NO_HOST_RESTORE = {"node": "legolas", "ok": False, "usb_config": "rndis,adb", "rndis0": "192.0.2.13/24",
                   "rndis0_state": "down", "offload": "off", "android": 11, "gadget_slots": ["ffs.adb", "rndis.gs4"],
                   "no_host": True, "why": "no USB host: ..."}


class NoUsbHost(unittest.TestCase):
    """A restore that proves there is no USB host is reported ONCE, in words, and not re-run while the tablet's
    controller still reads what it read then; any change in that state puts the node back on the normal path."""

    def setUp(self):
        for k in tc._KEEP:
            tc._KEEP[k].clear()
        self.restores, self.reading = [], dict(NO_HOST)

    def run_pass(self, t, result=NO_HOST_RESTORE):
        return [(n, a) for n, a, _ in tc.keep_usb_links(
            names=["legolas"], now_t=t, status_fn=lambda n: dict(self.reading),
            restore_fn=lambda n: (self.restores.append(t) or dict(result)), idle=lambda: True)]

    def test_reported_once_then_waits_quietly_past_the_cooldown(self):
        self.run_pass(0)
        self.assertEqual(self.run_pass(300), [("legolas", "no-usb-host")])
        for t in (600, 900, 300 + tc.USB_KEEP_COOLDOWN_S, 300 + 3 * tc.USB_KEEP_COOLDOWN_S):
            self.assertEqual(self.run_pass(t), [("legolas", "no-host-waiting")])
        self.assertEqual(self.restores, [300], "a restore cannot fix a missing host - it runs once, not every 30 min")

    def test_a_changed_controller_state_returns_to_the_normal_path(self):
        self.run_pass(0); self.run_pass(300)
        self.reading["udc_state"] = "configured"            # the cable was fixed: the host enumerated it
        self.assertEqual(self.run_pass(600), [("legolas", "seen")], "re-confirmed like any down link")
        self.run_pass(300 + tc.USB_KEEP_COOLDOWN_S)
        self.assertEqual(len(self.restores), 2, "back on the normal path, the restore runs again")

    def test_a_link_back_up_clears_it(self):
        self.run_pass(0); self.run_pass(300)
        self.reading = dict(NO_HOST, rndis0_state="up", udc_state="configured")
        self.assertEqual(self.run_pass(600), [])
        self.assertNotIn("legolas", tc._KEEP["no_host"])

    def test_an_ordinary_failure_still_retries_after_its_cooldown(self):
        plain = dict(NO_HOST_RESTORE, no_host=False)
        self.run_pass(0, plain)
        self.assertEqual(self.run_pass(300, plain), [("legolas", "restore-failed")])
        self.run_pass(600, plain)                              # re-confirming
        self.run_pass(300 + tc.USB_KEEP_COOLDOWN_S, plain)
        self.assertEqual(len(self.restores), 2)


class NoHostProbe(unittest.TestCase):
    """_no_host reads the gadget's UDC binding and the controller state; only BOUND + "not attached" proves it."""

    def probe(self, out):
        with mock.patch.object(tc, "_adb_sh", return_value=out):
            return tc._no_host("10.0.0.78")

    def test_bound_and_not_attached_is_no_host(self):
        self.assertIs(self.probe("BOUND=musb-hdrc\nSTATE=not attached"), True)

    def test_an_unbound_gadget_is_not_no_host(self):
        # an unbound gadget also reads "not attached" - and the restore's rebind IS its cure
        self.assertIs(self.probe("BOUND=\nSTATE=not attached"), False)

    def test_configured_is_not_no_host(self):
        self.assertIs(self.probe("BOUND=musb-hdrc\nSTATE=configured"), False)

    def test_unreadable_is_cannot_tell(self):
        self.assertIsNone(self.probe(""))

    def test_restore_reports_it_in_words(self):
        with mock.patch.object(tc, "_ensure_root", return_value=True), \
             mock.patch.object(tc, "_android", return_value=11), \
             mock.patch.object(tc, "_address", return_value=None), \
             mock.patch.object(tc, "_offload", return_value="off"), \
             mock.patch.object(tc, "_build_gadget", return_value=None), \
             mock.patch.object(tc, "_gadget_slots", return_value=["ffs.adb", "rndis.gs4"]), \
             mock.patch.object(tc, "_no_host", return_value=True), \
             mock.patch.object(tc, "status", return_value=NO_HOST):
            r = tc.usb_restore("legolas")
        self.assertFalse(r["ok"])
        self.assertTrue(r["no_host"])
        self.assertIn("no USB host", r["why"])


class RestoreUsesTheSameDerivation(unittest.TestCase):
    """usb_restore re-attaches the gadget only when the interface is down, and judges success with
    usb_link_down - the keeper's own detector. No subprocess runs: every node op is replaced."""

    def restore(self, readings, android=11):
        seq = list(readings)
        built = []
        self.fixed = []
        with mock.patch.object(tc, "_ensure_root", return_value=True), \
             mock.patch.object(tc, "_android", return_value=android), \
             mock.patch.object(tc, "_adb_sh", return_value="adb"), \
             mock.patch.object(tc, "_address", return_value=None), \
             mock.patch.object(tc, "_offload", side_effect=lambda ip, fix=False: self.fixed.append(fix) or "off"), \
             mock.patch.object(tc, "_build_gadget", side_effect=lambda ip, a: built.append(a)), \
             mock.patch.object(tc, "status", side_effect=lambda n: seq.pop(0) if len(seq) > 1 else seq[0]):
            r = tc.usb_restore("faramir")
        return r, built

    def test_offload_alone_fixes_offload_and_never_rebuilds_the_gadget(self):
        r, built = self.restore([dict(A11_UP, offload="on"), dict(A11_UP, offload="off")])
        self.assertEqual(built, [])
        self.assertEqual(self.fixed, [True])
        self.assertTrue(r["ok"])
        self.assertEqual(r["offload"], "off")

    def test_every_restore_turns_offload_off(self):
        self.restore([ADDR_BUT_DOWN, A11_UP])
        self.assertEqual(self.fixed, [True])

    def test_offload_that_will_not_turn_off_is_a_failure(self):
        r, _ = self.restore([dict(A11_UP, offload="on"), dict(A11_UP, offload="on")])
        self.assertFalse(r["ok"])

    def test_a_working_a11_link_is_not_bounced(self):
        r, built = self.restore([A11_UP, A11_UP])
        self.assertEqual(built, [])
        self.assertTrue(r["ok"])

    def test_a_down_link_is_rebuilt_and_reported_ok_once_up(self):
        r, built = self.restore([ADDR_BUT_DOWN, A11_UP])
        self.assertEqual(built, [11])
        self.assertTrue(r["ok"])

    def test_still_down_after_rebuild_is_a_failure(self):
        r, built = self.restore([ADDR_BUT_DOWN, ADDR_BUT_DOWN])
        self.assertEqual(built, [11])
        self.assertFalse(r["ok"])
        self.assertEqual(r["rndis0_state"], "down")


class GadgetBuild(unittest.TestCase):

    def build(self, android, slots_out, cfg="rndis,adb"):
        cmds = []

        def sh(ip, c, timeout=45):
            cmds.append(c)
            if c.startswith("getprop sys.usb.config"):
                return cfg
            if "echo SLOTS" in c:
                return slots_out
            return ""

        with mock.patch.object(tc, "_adb_sh", side_effect=sh), mock.patch.object(tc.time, "sleep"):
            tc._build_gadget("192.0.2.1", android)
        return cmds

    CLASS_A_STUCK = ("SLOTS\n../../../../usb_gadget/g1/functions/ffs.adb\n"
                     "../../../../usb_gadget/g1/functions/ffs.adb")             # gandalf, aragorn
    QCOM_A12_UP = ("SLOTS\n../../../../usb_gadget/g1/functions/rndis.rndis\n"
                   "../../../../usb_gadget/g1/functions/ffs.adb")               # merry
    MTK_A11_UP = ("SLOTS\n../../../../usb_gadget/g1/functions/ffs.adb\n"
                  "../../../../usb_gadget/g1/functions/rndis.gs4")              # frodo

    def test_slot_reader_parses_the_real_readlink_output(self):
        with mock.patch.object(tc, "_adb_sh", return_value=self.CLASS_A_STUCK):
            self.assertEqual(tc._gadget_slots("192.0.2.1"), ["ffs.adb", "ffs.adb"])
        with mock.patch.object(tc, "_adb_sh", return_value=self.QCOM_A12_UP):
            self.assertEqual(tc._gadget_slots("192.0.2.1"), ["rndis.rndis", "ffs.adb"])
        with mock.patch.object(tc, "_adb_sh", return_value="SLOTS"):
            self.assertEqual(tc._gadget_slots("192.0.2.1"), [])
        with mock.patch.object(tc, "_adb_sh", return_value="sh: cd: Permission denied"):
            self.assertIsNone(tc._gadget_slots("192.0.2.1"))

    def test_a12_with_adb_in_both_slots_is_relinked_after_the_setprop(self):
        cmds = self.build(12, self.CLASS_A_STUCK)
        self.assertIn("setprop sys.usb.config rndis,adb", cmds)
        self.assertEqual(cmds[-1], tc.RELINK)
        self.assertLess(cmds.index("setprop sys.usb.config rndis,adb"), cmds.index(tc.RELINK))

    def test_a12_whose_setprop_linked_rndis_is_not_relinked(self):
        self.assertNotIn(tc.RELINK, self.build(12, self.QCOM_A12_UP))

    def test_a12_relink_needs_a_readable_config_and_an_rndis_property(self):
        self.assertNotIn(tc.RELINK, self.build(12, "sh: cd: Permission denied"))
        self.assertNotIn(tc.RELINK, self.build(12, self.CLASS_A_STUCK, cfg="none"))

    def test_a11_always_relinks(self):
        cmds = self.build(11, self.MTK_A11_UP)
        self.assertEqual(cmds, [tc.RELINK])

    def test_the_relink_points_f2_at_rndis_and_rebinds(self):
        self.assertIn("ln -s $G/functions/rndis.gs4 $G/configs/b.1/f2", tc.RELINK)
        self.assertTrue(tc.RELINK.endswith("echo $U > $G/UDC"))
        self.assertNotIn("none", tc.RELINK)


def _series(pcts, status="Charging", run="", start=0.0, step=10.0):
    """Telemetry rows (ts, run, battery_pct, charging) every `step` s - the idle tablet period."""
    return [(start + i * step, run, p, status) for i, p in enumerate(pcts)]


H2 = int(tc.CHARGE_STALL_S // 10) + 1          # one 2 h window of idle samples


class ChargeVerdict(unittest.TestCase):
    now = H2 * 10.0

    def test_sam_flat_while_charging_is_stalled(self):
        self.assertIs(tc.charge_stalled(_series([16.0] * H2), self.now), True)

    def test_charging_and_falling_is_stalled(self):
        self.assertIs(tc.charge_stalled(_series([16.0 - i * 0.01 for i in range(H2)]), self.now), True)

    def test_merry_gaining_is_not(self):
        self.assertIs(tc.charge_stalled(_series([50.0 + i * 11.0 / 360 for i in range(H2)]), self.now), False)

    def test_one_point_of_gain_in_the_window_clears_it(self):
        self.assertIs(tc.charge_stalled(_series([16.0] * (H2 - 1) + [17.0]), self.now), False)

    def test_governor_held_discharging_is_not(self):
        self.assertIs(tc.charge_stalled(_series([84.0] * H2, status="Discharging"), self.now), False)

    def test_full_is_not(self):
        rows = [(i * 10.0, "", 100.0, "Full" if i % 2 else "Charging") for i in range(H2)]
        self.assertIs(tc.charge_stalled(rows, self.now), False)
        self.assertIs(tc.charge_stalled(_series([100.0] * H2), self.now), False)

    def test_an_order_in_the_window_is_cannot_tell(self):
        rows = _series([16.0] * H2)
        rows[5] = (rows[5][0], "rigby:bench-36-r30", 16.0, "Charging")
        self.assertIsNone(tc.charge_stalled(rows, self.now))

    def test_short_or_stale_coverage_is_cannot_tell(self):
        self.assertIsNone(tc.charge_stalled(_series([16.0] * 60, start=self.now - 600), self.now))   # 10 min only
        self.assertIsNone(tc.charge_stalled(_series([16.0] * (H2 - 120)), self.now))                # ends 20 min ago
        self.assertIsNone(tc.charge_stalled([], self.now))
        self.assertIsNone(tc.charge_stalled([(t, "", None, None) for t in range(0, int(self.now), 10)], self.now))


class ChargeKeeper(unittest.TestCase):
    def setUp(self):
        tc._CHARGE["acted"].clear()
        tc._CHARGE["reported"].clear()
        self.rebooted = []
        self.idle = True
        self.ok = True

    def run_pass(self, t, sam_pcts=None):
        sam = _series(sam_pcts or [16.0] * H2, start=t - H2 * 10.0 + 10.0)
        merry = _series([50.0 + i * 11.0 / 360 for i in range(H2)], start=t - H2 * 10.0 + 10.0)
        return tc.keep_charge(names=["sam", "merry"], now_t=t,
                              rows_fn=lambda names, since: {"sam": sam, "merry": merry},
                              reboot_fn=lambda n: (self.rebooted.append(n) or {"node": n, "ok": self.ok}),
                              idle=lambda: self.idle)

    def test_reboots_the_stalled_node_only(self):
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(10000.0)], [("sam", "rebooted")])
        self.assertEqual(self.rebooted, ["sam"])

    def test_never_reboots_while_an_order_runs(self):
        self.idle = False
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(10000.0)], [("sam", "deferred-busy")])
        self.assertEqual(self.rebooted, [])

    def test_not_cured_is_reported_once_and_never_rebooted_again_inside_the_cooldown(self):
        self.run_pass(10000.0)
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(20000.0)], [("sam", "not-cured")])
        self.assertEqual(self.run_pass(30000.0), [])
        self.assertEqual(self.rebooted, ["sam"])
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(10000.0 + tc.CHARGE_COOLDOWN_S)], [("sam", "rebooted")])

    def test_a_failed_reboot_is_reported_and_starts_the_cooldown(self):
        self.ok = False
        self.assertEqual([(n, a) for n, a, _ in self.run_pass(10000.0)], [("sam", "reboot-failed")])
        self.run_pass(20000.0)
        self.assertEqual(self.rebooted, ["sam"])

    def test_healthy_fleet_does_nothing(self):
        self.assertEqual(self.run_pass(10000.0, sam_pcts=[16.0 + i * 0.03 for i in range(H2)]), [])


class OffloadReading(unittest.TestCase):
    """_offload parses the script's --check line; the script and the parser must agree (the seam)."""

    def read(self, check_out, push_rc=0, fix=False):
        cmds = []
        with mock.patch.object(tc, "_adb", return_value=(push_rc, "", "")), \
             mock.patch.object(tc, "_adb_sh", side_effect=lambda ip, c, timeout=45: cmds.append(c) or check_out):
            return tc._offload("192.0.2.1", fix=fix), cmds

    def test_parses_on_off_and_refuses_to_guess(self):
        self.assertEqual(self.read("offload: on {'gso': 1, 'sg': 1}")[0], "on")
        self.assertEqual(self.read("offload: off {'gso': 0, 'sg': 0}")[0], "off")
        self.assertEqual(self.read("offload: ? {'gso': 'ERR:OSError'}")[0], "?")
        self.assertEqual(self.read("")[0], "?")
        self.assertEqual(self.read("offload: off {}", push_rc=1)[0], "?")

    def test_fix_sets_before_it_checks(self):
        _, cmds = self.read("offload: off {}", fix=True)
        self.assertEqual(len(cmds), 2)
        self.assertNotIn("--check", cmds[0])
        self.assertIn("--check", cmds[1])

    def test_the_real_script_speaks_the_parsers_format(self):
        import subprocess
        p = subprocess.run([sys.executable, tc.OFFLOAD_LOCAL, "lo", "--check"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        verdict, _ = self.read(p.stdout.strip())
        self.assertIn(verdict, ("on", "off"), "script said %r" % p.stdout)


if __name__ == "__main__":
    unittest.main()
