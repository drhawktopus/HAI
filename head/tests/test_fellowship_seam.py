"""Verify that the tablet_map tool remains wired in Kirk (the Orchestrator) and that per-frame detection is not reimplemented there. These tests inspect source files rather than importing them, because they validate implementation details of the Orchestrator codebase.

Note: The Fulfillment-side seam tests (consuming order files from tablet_map) reside in the Tolkien suite (tests/test_tablet_map.py)."""
import os
import unittest


class TabletMapToolWiringTests(unittest.TestCase):
    """web_search-style wiring checks from source, without importing tools.py."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.tools_source = Path("tools.py").read_text(encoding="utf-8")

    def test_schema_present(self):
        self.assertIn('"name": "tablet_map"', self.tools_source)

    def test_in_both_tool_lists(self):
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.tools_source.splitlines() if l.startswith(prefix))
            self.assertIn("_TABLET_MAP", line)

    def test_dispatch_present(self):
        self.assertIn('if name == "tablet_map":', self.tools_source)

    def test_admin_gated_and_sandbox_blocked(self):
        import re
        admin = re.search(r"_ADMIN_TOOLS = \{.*?\}", self.tools_source, re.S).group(0)
        self.assertIn('"tablet_map"', admin)
        sandbox = re.search(r"SANDBOX_BLOCKED_TOOLS = \{.*?\}", self.tools_source, re.S).group(0)
        self.assertIn('"tablet_map"', sandbox)


class ShimStaysOutOfKirkTests(unittest.TestCase):

    def test_shim_does_not_live_in_kirk(self):
        """The orchestrator must not carry a detection route.

        Per-frame camera traffic through Kirk measurably starved every other caller of that
        service (an unrelated /health went 15 ms -> 1887 ms). This asserts the module that did it
        is gone, so a future edit cannot quietly reintroduce the same building."""
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("fellowship_detect"),
                          "fellowship_detect is back — the shim belongs in Tolkien, not Kirk")
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "app.py"), encoding="utf-8") as f:
            app_src = f.read()
        self.assertNotIn("/fellowship/v1/vision/detection\"", app_src)
        self.assertNotIn("include_router(fellowship_detect", app_src)


if __name__ == "__main__":
    unittest.main()
