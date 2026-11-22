import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
from pathlib import Path


class AbandonWiringTests(unittest.TestCase):
    def test_client_routes_abandon(self):
        src = Path("coding_client.py").read_text(encoding="utf-8")
        self.assertIn('action == "abandon"', src)
        self.assertIn("/abandon", src)

    def test_tool_enum_exposes_abandon(self):
        src = Path("tools.py").read_text(encoding="utf-8")
        block = src.split("_CODING_TASK = {")[1].split("required")[0]
        self.assertIn('"abandon"', block)


if __name__ == "__main__":
    unittest.main()