"""gate_pin.OPTIONAL_TESTS must be true: each optional file present holds exactly the tests the pin says, so a head
without it subtracts the right number. A wrong count fails here, in any tree that carries the file."""
import os
import unittest

import gate_pin

HERE = os.path.dirname(os.path.abspath(__file__))


def _count(suite):
    return sum(_count(t) if isinstance(t, unittest.TestSuite) else 1 for t in suite)


class OptionalTestCounts(unittest.TestCase):
    def test_every_present_optional_file_holds_the_pinned_count(self):
        loader = unittest.TestLoader()
        checked = 0
        for name, pinned in gate_pin.OPTIONAL_TESTS.items():
            if not os.path.exists(os.path.join(HERE, name)):
                continue
            got = _count(loader.loadTestsFromName(name[:-3]))
            self.assertEqual(got, pinned, "%s holds %d tests; gate_pin.OPTIONAL_TESTS says %d" % (name, got, pinned))
            checked += 1
        if not checked:
            self.skipTest("this head carries none of the optional test files: no count to check")
