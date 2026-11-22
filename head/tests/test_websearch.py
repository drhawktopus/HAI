"""Unit tests for websearch module and static tool wiring.

The runner test image does not include every orchestrator dependency, so the
websearch HTTP dependency is mocked and tool wiring is verified from source.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


class _FakeHttpx:
    """Stand-in for httpx so websearch can import without the real package."""
    get = MagicMock()


sys.modules.pop("websearch", None)
_real_httpx = sys.modules.get("httpx")
sys.modules["httpx"] = _FakeHttpx()


class MockResponse:
    """Minimal httpx.Response stand-in with raise_for_status and json."""

    def __init__(self, body=None, status_code=200):
        self.status_code = status_code
        self._body = body if body is not None else {"results": []}

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._body


import websearch  # noqa: E402

# Restore real httpx to prevent module cache poisoning for subsequent imports.
if _real_httpx is not None:
    sys.modules["httpx"] = _real_httpx
else:
    sys.modules.pop("httpx", None)



class WebsearchSearchTests(unittest.TestCase):
    """Test websearch.search() without hitting the network."""

    def setUp(self):
        _FakeHttpx.get.reset_mock(side_effect=True, return_value=True)
        websearch.MIN_INTERVAL_S = 0.0  # no pacing sleeps in unit tests

    def test_successful_results_parsed(self):
        body = {"results": [
            {"title": "Result One", "url": "https://example.com/1", "content": "First snippet text"},
            {"title": "Result Two", "url": "https://example.com/2", "content": "Second snippet text here."},
        ]}
        _FakeHttpx.get.return_value = MockResponse(body)

        result = websearch.search("test query")

        self.assertIn("Result One", result)
        self.assertIn("https://example.com/1", result)
        self.assertIn("First snippet text", result)
        self.assertIn("Result Two", result)
        _FakeHttpx.get.assert_called_once()
        call_kwargs = _FakeHttpx.get.call_args.kwargs
        self.assertEqual(call_kwargs.get("params", {}).get("q"), "test query")
        self.assertEqual(call_kwargs.get("params", {}).get("format"), "json")

    def test_no_results_returns_message(self):
        _FakeHttpx.get.return_value = MockResponse({"results": []})

        result = websearch.search("impossible query")

        self.assertIn("no results", result.lower())
        self.assertIn("impossible query", result)

    def test_empty_with_suspended_engines_reports_throttle_not_no_results(self):
        _FakeHttpx.get.return_value = MockResponse({
            "results": [],
            "unresponsive_engines": [["brave", "too many requests"], ["duckduckgo", "CAPTCHA"]],
        })

        result = websearch.search("throttled query")

        self.assertIn("THROTTLED", result)
        self.assertIn("brave", result)
        self.assertNotIn("no results", result.lower())

    def test_empty_without_suspended_engines_still_says_no_results(self):
        _FakeHttpx.get.return_value = MockResponse({"results": [], "unresponsive_engines": []})

        result = websearch.search("genuinely empty")

        self.assertIn("no results", result.lower())

    def test_missing_content_graceful(self):
        _FakeHttpx.get.return_value = MockResponse({
            "results": [{"title": "Minimal", "url": "https://example.com/min"}]
        })

        result = websearch.search("minimal")

        self.assertIn("Minimal", result)
        self.assertIn("https://example.com/min", result)

    def test_max_results_limits(self):
        _FakeHttpx.get.return_value = MockResponse({
            "results": [
                {"title": f"Item {i}", "url": f"https://example.com/{i}"}
                for i in range(10)
            ]
        })

        result = websearch.search("many", max_results=3)

        self.assertIn("Item 0", result)
        self.assertIn("Item 1", result)
        self.assertIn("Item 2", result)
        self.assertNotIn("Item 3", result)

    def test_http_error_returns_failure_string(self):
        _FakeHttpx.get.return_value = MockResponse(status_code=500)

        result = websearch.search("error")

        self.assertTrue(result.startswith("Web search failed:"))

    def test_exception_returns_failure_string(self):
        _FakeHttpx.get.side_effect = Exception("connection refused")

        result = websearch.search("boom")

        self.assertTrue(result.startswith("Web search failed:"))

    def test_snippet_truncated(self):
        long_content = "A" * 300
        _FakeHttpx.get.return_value = MockResponse({
            "results": [{"title": "Long", "url": "https://example.com/long", "content": long_content}]
        })

        result = websearch.search("long")

        self.assertNotIn(long_content, result)


class WebsearchToolWiringTests(unittest.TestCase):
    """Verify web_search is wired into tool schemas and dispatch without importing tools.py."""

    @classmethod
    def setUpClass(cls):
        cls.tools_source = Path("tools.py").read_text(encoding="utf-8")
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.config_source = Path("config.py").read_text(encoding="utf-8")

    def test_web_search_schema_present(self):
        self.assertIn("_WEB_SEARCH", self.tools_source)
        self.assertIn('"name": "web_search"', self.tools_source)
        self.assertIn('"query"', self.tools_source)

    def test_web_search_in_brain_and_heavy_tools(self):
        # Robust to neighbor changes: assert membership in each tool list line.
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.tools_source.splitlines() if l.startswith(prefix))
            self.assertIn("_WEB_SEARCH", line)

    def test_web_search_dispatch_present(self):
        self.assertIn('if name == "web_search":', self.tools_source)
        self.assertIn('websearch.search(args.get("query", ""))', self.tools_source)

    def test_system_prompt_mentions_web_search(self):
        self.assertIn("web_search", self.app_source)
        self.assertIn("search the public internet", self.app_source)

    def test_searxng_url_configured(self):
        self.assertIn("SEARXNG_URL", self.config_source)
        self.assertIn("http://searxng:8080", self.config_source)


if __name__ == "__main__":
    unittest.main()
