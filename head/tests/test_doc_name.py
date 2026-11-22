"""Test that user-facing prompts use "Doc" for the personality and "HAI" for the architecture."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

PROMPTS = ("SYSTEM", "NURSING_BASE", "LAW_SYSTEM", "HEAVY_CONTINUATION")


class TheAssistantIsDocRunningOnHAI(unittest.TestCase):
    def test_every_prompt_names_doc_and_none_says_home_ai(self):
        for name in PROMPTS:
            text = getattr(app, name)
            self.assertIn("Doc", text, name)
            self.assertNotIn("Home AI", text, name)
        self.assertIn("Doc, running on HAI, Alex's home lab system", app.SYSTEM)

    def test_the_chat_page_says_doc_where_it_names_who_you_talk_to(self):
        page = app.CHAT_HTML
        self.assertIn("<title>Doc</title>", page)
        self.assertIn('<span class="brand">🏠 Doc ', page)
        self.assertNotIn("Home AI", page)
        self.assertNotIn("Sent while HAI was working", page)

    def test_a_request_to_change_hai_reads_as_self_engineering_and_the_word_doc_does_not(self):
        intent = app.SELF_ENGINEERING_INTENT
        self.assertTrue(intent.search("Please add a read-only tool to HAI"))
        self.assertTrue(intent.search("fix HAI's router"))
        self.assertFalse(intent.search("fix the doc for the router"), "an everyday 'doc' is not the system")
        self.assertFalse(intent.search("How is HAI doing today?"))
        self.assertTrue(intent.search("Please add a read-only tool to Doc"))
        self.assertTrue(intent.search("Doc, fix your router"))
        self.assertFalse(intent.search("fix the Doc page for the router"))
        self.assertFalse(intent.search("add a doc to the library"))
        self.assertFalse(intent.search("How is Doc doing today?"))


if __name__ == "__main__":
    unittest.main()
