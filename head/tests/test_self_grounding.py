import os
import tempfile
import unittest

import grounding


_SAMPLE = '''\
"""Sample module for grounding tests."""
import asyncio


async def query_stream(request):
    """Handle the streaming connection: on client disconnect cancel the running worker so the
    mothership slot is freed. Uses asyncio.to_thread to run the worker off the event loop."""
    return await asyncio.to_thread(_work, request)


class CancellationRegistry:
    """Tracks in-flight runs and cancels a worker when the client disconnects mid stream."""
    def cancel(self, run_id):
        return run_id


def photosynthesis_unrelated(light, water):
    """A totally unrelated chlorophyll routine that shares no terms with the others."""
    return light + water
'''


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "sample.py"), "w", encoding="utf-8") as fh:
            fh.write(_SAMPLE)
        grounding.configure([self.tmp])

    def test_symbol_chunking(self):
        chunks = grounding._symbol_chunks("sample.py", _SAMPLE)
        syms = {c[1].splitlines()[0] for c in chunks}
        self.assertIn("# sample.py :: query_stream", syms)
        self.assertIn("# sample.py :: CancellationRegistry", syms)

    def test_retrieves_relevant_symbols(self):
        hits = grounding.retrieve("streaming disconnect cancel running worker mothership")
        self.assertTrue(hits)
        joined = " ".join(h["text"] for h in hits)
        self.assertTrue("query_stream" in joined or "CancellationRegistry" in joined)

    def test_greenfield_query_returns_empty(self):
        # shares no terms with the code -> nothing relevant -> ungrounded
        self.assertEqual(grounding.retrieve("quarterly tax invoice spreadsheet export"), [])

    def test_context_block_has_header_and_code(self):
        block = grounding.context_block("streaming disconnect cancel worker")
        self.assertIn("Relevant EXISTING code", block)
        self.assertIn("sample.py", block)

    def test_context_block_empty_for_greenfield(self):
        self.assertEqual(grounding.context_block("quarterly tax invoice spreadsheet"), "")

    def test_context_block_respects_cap(self):
        block = grounding.context_block("streaming disconnect cancel worker", max_chars=200)
        self.assertLessEqual(len(block), 200)

    def test_sources_lists_files(self):
        self.assertEqual(grounding.sources("streaming disconnect cancel worker"), ["sample.py"])


if __name__ == "__main__":
    unittest.main()
