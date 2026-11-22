"""Where McCoy stays loaded (config.mccoy_resident_hosts): never on a one-device head, where he would hold room the
person's own model needs and each would keep reloading the other; on the advisor's own box in a lab with a roster;
and exactly where the MCCOY_RESIDENT_HOSTS setting says when it is set."""
import unittest

import config


class WhereMcCoyStaysLoaded(unittest.TestCase):
    def test_a_one_device_head_keeps_him_resident_nowhere(self):
        self.assertEqual(config.mccoy_resident_hosts(None, True, "local"), set())

    def test_a_lab_keeps_him_on_the_advisors_own_box(self):
        self.assertEqual(config.mccoy_resident_hosts(None, False, "softserve"), {"softserve"})

    def test_the_setting_wins_and_empty_means_nowhere(self):
        self.assertEqual(config.mccoy_resident_hosts("a, b", False, "softserve"), {"a", "b"})
        self.assertEqual(config.mccoy_resident_hosts("", False, "softserve"), set())
        self.assertEqual(config.mccoy_resident_hosts("a", True, "local"), {"a"})

    def test_a_roster_with_no_advisor_keeps_him_resident_nowhere(self):
        self.assertEqual(config.mccoy_resident_hosts(None, False, None), set())


if __name__ == "__main__":
    unittest.main()
