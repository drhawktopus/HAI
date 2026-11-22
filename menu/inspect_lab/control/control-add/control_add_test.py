import unittest

from control_add import add


class ControlAddTest(unittest.TestCase):
    def test_small(self):
        self.assertEqual(add(1, 2), 3)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)

    def test_negative(self):
        self.assertEqual(add(-4, 9), 5)

    def test_large(self):
        self.assertEqual(add(10 ** 12, 1), 10 ** 12 + 1)


if __name__ == "__main__":
    unittest.main()
