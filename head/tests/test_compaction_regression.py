import unittest
from pathlib import Path


class CompactionRegressionTests(unittest.TestCase):
    def test_compaction_worker_uses_defined_helper(self):
        src = Path("app.py").read_text(encoding="utf-8")
        self.assertNotIn("softserve_allowed", src, "defect #1 regression: undefined softserve_allowed() is back")
        self.assertIn("def failover_available", src)


if __name__ == "__main__":
    unittest.main()