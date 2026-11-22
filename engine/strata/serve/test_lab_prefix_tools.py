"""LAB patch strata_prefix_tools_patch.py: {"tools": K} pins the start every tool list beginning with those K tools shares."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve.frontend import ChatTemplate  # noqa: E402
from serve.server import ByteTokenizer, MockEngine, Service  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CTX = 32768


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "does " + name,
                                             "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}}


class ThePrefixEndsInsideTheToolList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.svc = Service(MockEngine(tok, "ok", max_context=CTX), tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        cls.tok = tok

    MSGS = [{"role": "system", "content": "You are HAI."}, {"role": "user", "content": "hello"}]

    def pin(self, tools, k):
        req = {"strata_prefix": {"tools": k}}
        ids, _, _ = self.svc.prepare(self.MSGS, tools, {}, 16, req=req)
        return ids, req["strata_prefix"]

    def test_the_start_two_lists_share_through_their_first_k_tools_is_one_pin(self):
        core = [tool("search_docs"), tool("get_document")]
        ids_a, pin_a = self.pin(core + [tool("coding_test")], 2)
        ids_b, pin_b = self.pin(core + [tool("weather_now"), tool("plex_sections")], 2)
        self.assertTrue(pin_a.get("tokens") and pin_a == pin_b, (pin_a, pin_b))
        n = pin_a["tokens"]
        self.assertEqual(ids_a[:n], ids_b[:n], "both chats start with the same N tokens: one saved state serves them")
        shared = self.tok.decode(ids_a[:n], errors="replace")
        self.assertIn("get_document", shared)
        self.assertNotIn("coding_test", shared)
        self.assertNotIn("weather_now", shared)
        # the pin sits at the boundary before the next tool, whatever that tool is (both next tools open alike, then part)
        self.assertTrue(self.tok.decode(ids_a[n:], errors="replace").lstrip().startswith('{"type"'),
                        self.tok.decode(ids_a[n:n + 40], errors="replace"))
        self.assertIn("coding_test", self.tok.decode(ids_a[n:], errors="replace"))
        self.assertIn("weather_now", self.tok.decode(ids_b[n:], errors="replace"))

    def test_all_the_tools_shared_reaches_the_end_of_the_list_and_more_than_the_list_is_ignored(self):
        tools = [tool("search_docs"), tool("get_document")]
        _ids, pin = self.pin(tools, 2)
        self.assertTrue(pin.get("tokens"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.pin(tools, 3)[1], {}, "said in the log, never a refusal")
            req = {"strata_prefix": {"tools": 1}}
            self.svc.prepare(self.MSGS, None, {}, 16, req=req)
            self.assertEqual(req["strata_prefix"], {}, "a request with no tools has nothing to pin")
        for bad in ({"tools": 0}, {"tools": True}, {"tools": "2"}, {"tools": 1, "tokens": 2}):
            with self.assertRaises(ValueError, msg=bad):
                self.svc.prepare(self.MSGS, tools, {}, 16, req={"strata_prefix": bad})


if __name__ == "__main__":
    unittest.main()
