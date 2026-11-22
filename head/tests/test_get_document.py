"""Tests for the get_document tool (fetch a WHOLE library document, not just search_docs excerpts)."""
import tempfile
import unittest
from pathlib import Path

import config
import tools

APP = Path("app.py").read_text(encoding="utf-8")
TOOLS = Path("tools.py").read_text(encoding="utf-8")


class GetDocumentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        Path(self.dir, "riley-critical-appraisal-draft.md").write_text(
            "# Critique\nThis is the full critique text body.", encoding="utf-8")
        Path(self.dir, "riley-capstone-part1-draft.md").write_text(
            "# Capstone\nThe capstone proposal full text.", encoding="utf-8")
        self._orig = config.RAG_NAMESPACES
        config.RAG_NAMESPACES = dict(config.RAG_NAMESPACES)
        config.RAG_NAMESPACES["nursing"] = [self.dir]

    def tearDown(self):
        config.RAG_NAMESPACES = self._orig

    def test_exact_and_substring_match_returns_full_text(self):
        out = tools._get_document("Riley", "riley-critical-appraisal-draft.md")
        self.assertIn("full critique text body", out)
        self.assertIn("Full text of riley-critical-appraisal-draft.md", out)
        self.assertIn("full critique text body", tools._get_document("Riley", "critical"))

    def test_ambiguous_lists_choices(self):
        out = tools._get_document("Riley", "draft").lower()  # matches both files
        self.assertIn("which one", out)
        self.assertIn("riley-critical-appraisal-draft.md", out)
        self.assertIn("riley-capstone-part1-draft.md", out)

    def test_no_match_lists_library(self):
        self.assertIn("No document matched", tools._get_document("Riley", "nonexistent-xyz"))

    def test_empty_lists_library(self):
        self.assertIn("Which document", tools._get_document("Riley", ""))

    def test_a_long_document_comes_in_pages_that_rebuild_the_whole_text(self):
        # a page names the characters shown, the length and the next offset; paged to the end, the text is exact
        orig = config.GET_DOC_MAX_CHARS
        config.GET_DOC_MAX_CHARS = 20
        try:
            whole = tools._get_document("Riley", "capstone", None, 0)
            self.assertIn("characters 0-20 of", whole)
            self.assertIn("offset=20", whole)
            import re as _re
            total = int(_re.search(r"characters 0-20 of (\d+)", whole).group(1))
            got, offset, pages = "", 0, 0
            while True:
                page = tools._get_document("Riley", "capstone", None, offset)
                pages += 1
                head, body = page.split("\n\n", 1)
                m = _re.search(r"characters (\d+)-(\d+) of (\d+)", head)
                self.assertEqual((int(m.group(1)), int(m.group(3))), (offset, total), head)
                if "next offset=" in body:
                    body = body[:body.rindex("\n\n…[page ends")]
                got += body
                if "last page" in head:
                    break
                offset = int(m.group(2))
            self.assertEqual(len(got), total)
            self.assertGreater(pages, 1)
            self.assertIn("past its end", tools._get_document("Riley", "capstone", None, total + 5))
        finally:
            config.GET_DOC_MAX_CHARS = orig
        self.assertTrue(tools._get_document("Riley", "capstone").startswith("[Full text of"), "under the cap: whole, as before")

    def test_no_library_for_ungrounded_user(self):
        self.assertIn("no documents", tools._get_document("SomeRandomGuest", "x").lower())


class GetDocumentWiringTests(unittest.TestCase):
    def test_tool_registered_and_dispatched(self):
        self.assertIn('"name": "get_document"', TOOLS)
        self.assertIn("_GET_DOCUMENT,", TOOLS)            # in BRAIN/HEAVY tool lists
        self.assertIn('if name == "get_document":', TOOLS)
        self.assertIn("def _get_document(user, filename, persona=None, offset=0):", TOOLS)
        self.assertIn('args.get("offset", 0)', TOOLS)

    def test_prompt_points_to_get_document(self):
        self.assertIn("call `get_document`", APP)


if __name__ == "__main__":
    unittest.main()
