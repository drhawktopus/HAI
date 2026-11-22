"""Verify that the orientation tool ('catch_up') remains in the fallback set when embeddings are disabled,
preventing loss of context recovery during index outages.

This test exercises the EMBED_ENABLED=0 path (the gate environment) to ensure CORE_TOOL_NAMES includes
catch_up. The embeddings-enabled retrieval assertions are covered by selector_probe.py."""
import unittest

import config
import tool_selection
import tools


class CoreToolFloor(unittest.TestCase):
    def test_orientation_tools_are_core(self):
        for name in ("catch_up", "get_document", "search_docs"):
            self.assertIn(name, tool_selection.CORE_TOOL_NAMES,
                          "%s must survive an embedding-index outage" % name)

    def test_fallback_offers_catch_up_end_to_end(self):
        """Force the narrowing path with a tiny budget; with no embedding index (gate env), the
        selector falls back to CORE tools — catch_up must be in the offered set."""
        selected, note = tool_selection.select(
            tools.HEAVY_TOOLS, "Alex", "what did Claude task you with this morning?", 4096)
        names = {t["function"]["name"] for t in selected}
        self.assertLess(len(selected), len(tools.HEAVY_TOOLS),
                        "fixture is wrong if no narrowing happened - shrink the budget")
        self.assertIn("catch_up", names)
        self.assertIn("get_document", names)

    def test_search_comes_with_the_page_reader(self):
        """web_search and fetch_url are paired: web_search identifies pages while fetch_url retrieves them.
        Selecting one without the other degrades research to snippets only."""
        selected, _ = tool_selection.select(
            tools.HEAVY_TOOLS, "Alex", "research the FSR 4 SDK's inputs and read its docs page", 4096)
        names = {t["function"]["name"] for t in selected}
        self.assertLess(len(selected), len(tools.HEAVY_TOOLS), "fixture is wrong if no narrowing happened")
        self.assertIn("web_search", names)
        self.assertIn("fetch_url", names)


if __name__ == "__main__":
    unittest.main()
