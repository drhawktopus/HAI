"""The tablets' wire subnet is the TABLET_WIRE_PREFIX setting: a node's wire address is the prefix plus its roster
octet; with no prefix there is no wire, every node is reached over WiFi and no address is probed; the tablet-side
addressing command carries the configured subnet and nothing else."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as tf  # noqa: E402
import tablet_control as tc  # noqa: E402


class TheWireSubnetIsASetting(unittest.TestCase):
    def setUp(self):
        tf._ROUTE.clear()

    def test_no_prefix_means_no_wire_and_no_probe(self):
        with mock.patch.object(tf, "WIRE_PREFIX", ""), mock.patch.object(tf, "ROUTE_PROBE", True), \
                mock.patch.object(tf.socket, "socket", side_effect=AssertionError("probed with no wire")):
            self.assertIsNone(tf.wire_ip("frodo"))
            self.assertFalse(tf.on_wire("192.0.2.11"))
            self.assertEqual(tf.node_ip("frodo", 8022), (tf._wifi_ip("frodo"), "wifi"))

    def test_a_prefix_composes_the_wire_address_from_the_roster_octet(self):
        with mock.patch.object(tf, "WIRE_PREFIX", "192.0.2."):
            self.assertEqual(tf.wire_ip("frodo"), "192.0.2.%d" % tf.WIRE_OCTET["frodo"])
            self.assertIsNone(tf.wire_ip("no-such-node"))
            self.assertTrue(tf.on_wire("192.0.2.11"))
            self.assertFalse(tf.on_wire("10.1.0.11"))

    def test_the_tablet_side_addressing_uses_the_configured_subnet(self):
        sent = []
        with mock.patch.object(tf, "WIRE_PREFIX", "192.0.2."), mock.patch.object(tf, "WIRE_CIDR", "192.0.2.0/24"), \
                mock.patch.object(tc, "_adb_sh", side_effect=lambda ip, cmd, timeout=0: sent.append(cmd)):
            tc._address("192.0.2.9", 11)
        self.assertEqual(len(sent), 1)
        self.assertIn("addr replace 192.0.2.11/24 dev rndis0", sent[0])
        self.assertIn("route replace 192.0.2.0/24 dev rndis0 src 192.0.2.11 table 55", sent[0])
        self.assertNotIn("10.55.", sent[0])
        with mock.patch.object(tf, "WIRE_PREFIX", ""), mock.patch.object(tc, "_adb_sh", side_effect=AssertionError("ran")):
            with self.assertRaises(RuntimeError):
                tc._address("192.0.2.9", 11)


if __name__ == "__main__":
    unittest.main()
