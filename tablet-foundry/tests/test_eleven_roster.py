"""Pins the authoritative 11-member Fellowship roster across scheduler and Tolkien."""

import unittest

import scheduler
import tablet_control
import tablet_foundry


EXPECTED = {
    "gandalf", "frodo", "sam", "merry", "pippin", "legolas", "aragorn",
    "smeagol", "gimli", "boromir", "faramir",
}


class ElevenMemberRosterTests(unittest.TestCase):
    def test_all_authoritative_runtime_rosters_match(self):
        self.assertEqual(set(scheduler.TABLETS), EXPECTED)
        self.assertEqual(set(tablet_foundry.TABLET_NET), EXPECTED)
        self.assertEqual(set(tablet_control.SEG_OCTET), EXPECTED)

    def test_addresses_serials_and_usb_octets_are_unique(self):
        # the addresses and serials are the TABLET_NET setting (the gate gives a documentation-range roster)
        self.assertEqual(len({ip for ip, _serial in tablet_foundry.TABLET_NET.values()}), 11)
        self.assertEqual(len({serial for _ip, serial in tablet_foundry.TABLET_NET.values()}), 11)
        self.assertEqual(len(set(tablet_control.SEG_OCTET.values())), 11)

    def test_the_head_roster_carries_no_address_or_serial(self):
        for name, row in scheduler.TABLETS.items():
            self.assertNotIn("ip", row, name)
            self.assertNotIn("serial", row, name)

    def test_the_roster_setting_parses_and_refuses_junk(self):
        table = tablet_foundry._tablet_net('{"gimli": ["10.0.0.96", "S1"]}')
        self.assertEqual(table, {"gimli": ("10.0.0.96", "S1")})
        for junk in ("", "not json", '{"gimli": "10.0.0.96"}', "[1, 2]"):
            self.assertEqual(tablet_foundry._tablet_net(junk), {}, junk)

    def test_new_class_b_members_have_truthful_inherited_profile(self):
        for name, octet in {"gimli": 19, "boromir": 20, "faramir": 21}.items():
            row = scheduler.TABLETS[name]
            self.assertEqual((row["cpu"], row["ram_gb"], row["gflops"]),
                             (8, 1.76, 17.27))
            self.assertTrue(row["provisional"])
            self.assertIn(name, tablet_foundry.TABLET_NET)
            self.assertEqual(tablet_control.SEG_OCTET[name], octet)
            self.assertIn(name, tablet_control.GROUPS["class-b"])


if __name__ == "__main__":
    unittest.main()
