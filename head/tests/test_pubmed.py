"""Unit tests for the pubmed evidence module + static tool wiring.

The runner test image does not include httpx, so it's mocked and tool wiring is verified from source
(same pattern as test_websearch.py).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


class _FakeHttpx:
    get = MagicMock()


sys.modules.pop("pubmed", None)
_real_httpx = sys.modules.get("httpx")
sys.modules["httpx"] = _FakeHttpx()


class MockResponse:
    def __init__(self, body=None, status_code=200, content=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._body


_EFETCH_XML = (
    b"<?xml version='1.0'?><PubmedArticleSet>"
    b"<PubmedArticle><MedlineCitation><PMID>111</PMID><Article><Abstract>"
    b"<AbstractText>Simulation significantly improved student competence.</AbstractText>"
    b"</Abstract></Article></MedlineCitation></PubmedArticle>"
    b"<PubmedArticle><MedlineCitation><PMID>222</PMID><Article><Abstract>"
    b"<AbstractText Label='RESULTS'>Debriefing enhanced reflection.</AbstractText>"
    b"</Abstract></Article></MedlineCitation></PubmedArticle>"
    b"</PubmedArticleSet>"
)


import pubmed  # noqa: E402

# Restore real httpx to prevent module-level caching from poisoning other imports/tests.
if _real_httpx is not None:
    sys.modules["httpx"] = _real_httpx
else:
    sys.modules.pop("httpx", None)


pubmed._pause = lambda *a, **k: None  # don't actually sleep during throttle/backoff in tests


_SUMMARY = {"result": {
    "uids": ["111", "222"],
    "111": {
        "title": "Effect of high-fidelity simulation on nursing students.",
        "authors": [{"name": "Smith JA", "authtype": "Author"},
                    {"name": "Doe MB", "authtype": "Author"}],
        "fulljournalname": "Journal of Nursing Education",
        "source": "J Nurs Educ",
        "pubdate": "2023 May",
        "volume": "62", "issue": "5", "pages": "270-275",
        "articleids": [{"idtype": "doi", "value": "10.1234/jne.2023"},
                       {"idtype": "pmc", "value": "PMC123456"}],
    },
    "222": {
        "title": "Debriefing methods review.",
        "authors": [{"name": "Lee K", "authtype": "Author"}],
        "fulljournalname": "Nurse Education Today",
        "pubdate": "2024", "volume": "70", "pages": "1-9",
        "articleids": [{"idtype": "doi", "value": "10.5678/net.2024"}],
    },
}}


def _router(idlist=("111", "222")):
    def _get(url, params=None, timeout=None, **kw):
        if "esearch" in url:
            return MockResponse({"esearchresult": {"idlist": list(idlist)}})
        if "esummary" in url:
            return MockResponse(_SUMMARY)
        return MockResponse({}, 404)
    return _get


class PubmedSearchTests(unittest.TestCase):
    def setUp(self):
        _FakeHttpx.get.reset_mock(side_effect=True, return_value=True)

    def test_parses_and_builds_apa_reference(self):
        _FakeHttpx.get.side_effect = _router()
        out = pubmed.search("nursing simulation", max_results=2, years=5)
        self.assertIn("Smith, J. A., & Doe, M. B.", out)
        self.assertIn("(2023).", out)
        self.assertIn("Journal of Nursing Education, 62(5), 270-275.", out)
        self.assertIn("https://doi.org/10.1234/jne.2023", out)
        self.assertIn("PMID 111", out)
        self.assertIn("PMC123456", out)
        self.assertIn("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/", out)
        self.assertNotIn("relaxed", out)  # tier-1 (free full text) hit is not labeled relaxed

    def test_relaxes_when_free_full_text_empty(self):
        # First tier (free full text) returns nothing; the relaxed tier (no free-full-text) returns a hit.
        def _get(url, params=None, timeout=None, **kw):
            if "esearch" in url:
                idlist = [] if "free full text[Filter]" in params["term"] else ["222"]
                return MockResponse({"esearchresult": {"idlist": idlist}})
            if "esummary" in url:
                return MockResponse(_SUMMARY)
            return MockResponse({}, 404)
        _FakeHttpx.get.side_effect = _get
        out = pubmed.search("over specified rare topic")
        self.assertIn("Lee, K.", out)               # surfaced a real result from the relaxed tier
        self.assertIn("filters relaxed", out)        # header flags the relaxation
        self.assertIn("full text via CINAHL/library", out)  # no-PMC result is labeled for retrieval

    def test_falls_back_to_older_with_flag(self):
        # All date-limited tiers empty; the no-date tier returns older articles -> surfaced WITH a warning.
        def _get(url, params=None, timeout=None, **kw):
            if "esearch" in url:
                idlist = [] if "[PDat]" in params["term"] else ["222"]
                return MockResponse({"esearchresult": {"idlist": idlist}})
            if "esummary" in url:
                return MockResponse(_SUMMARY)
            return MockResponse({}, 404)
        _FakeHttpx.get.side_effect = _get
        out = pubmed.search("very old niche topic")
        self.assertIn("Lee, K.", out)   # older result surfaced rather than a dead end
        self.assertIn("OLDER", out)     # explicitly flagged as exceeding the recency window

    def test_includes_abstracts(self):
        def _get(url, params=None, timeout=None, **kw):
            if "esearch" in url:
                return MockResponse({"esearchresult": {"idlist": ["111", "222"]}})
            if "esummary" in url:
                return MockResponse(_SUMMARY)
            if "efetch" in url:
                return MockResponse(content=_EFETCH_XML)
            return MockResponse({}, 404)
        _FakeHttpx.get.side_effect = _get
        out = pubmed.search("simulation", 2)
        self.assertIn("Abstract: Simulation significantly improved student competence", out)
        self.assertIn("RESULTS: Debriefing enhanced reflection", out)

    def test_abstract_unavailable_warns(self):
        _FakeHttpx.get.side_effect = _router()  # no efetch branch -> 404 -> abstracts {}
        out = pubmed.search("topic", 2)
        self.assertIn("Abstract: (unavailable", out)

    def test_single_author_no_ampersand(self):
        _FakeHttpx.get.side_effect = _router(idlist=("222",))
        out = pubmed.search("debriefing")
        self.assertIn("Lee, K.", out)
        self.assertNotIn("& Lee", out)

    def test_recency_and_fulltext_filters_in_term(self):
        _FakeHttpx.get.side_effect = _router()
        pubmed.search("sepsis", years=5)
        term = _FakeHttpx.get.call_args_list[0].kwargs["params"]["term"]
        self.assertIn('"last 5 years"[PDat]', term)
        self.assertIn("free full text[Filter]", term)

    def test_max_results_passed_as_retmax(self):
        _FakeHttpx.get.side_effect = _router()
        pubmed.search("stroke", max_results=3)
        self.assertEqual(_FakeHttpx.get.call_args_list[0].kwargs["params"]["retmax"], 3)

    def test_retries_on_429_then_succeeds(self):
        calls = {"n": 0}

        def _get(url, params=None, timeout=None, **kw):
            if "esearch" in url:
                calls["n"] += 1
                if calls["n"] == 1:
                    return MockResponse(status_code=429)  # first try rate-limited
                return MockResponse({"esearchresult": {"idlist": ["222"]}})
            if "esummary" in url:
                return MockResponse(_SUMMARY)
            return MockResponse({}, 404)
        _FakeHttpx.get.side_effect = _get
        out = pubmed.search("topic")
        self.assertIn("Lee, K.", out)  # recovered after the 429 backoff-retry

    def test_no_results_message(self):
        _FakeHttpx.get.side_effect = _router(idlist=())
        out = pubmed.search("zzzznotopic")
        self.assertIn("No peer-reviewed", out)
        self.assertIn("do NOT assert a fact without a real source", out)

    def test_empty_query(self):
        self.assertIn("needs a query", pubmed.search("  "))

    def test_http_error_failsoft(self):
        _FakeHttpx.get.return_value = MockResponse(status_code=500)
        _FakeHttpx.get.side_effect = None
        self.assertTrue(pubmed.search("x").startswith("Evidence search failed:"))

    def test_exception_failsoft(self):
        _FakeHttpx.get.side_effect = Exception("connection refused")
        self.assertTrue(pubmed.search("x").startswith("Evidence search failed:"))


class PubmedToolWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools_source = Path("tools.py").read_text(encoding="utf-8")
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.config_source = Path("config.py").read_text(encoding="utf-8")

    def test_schema_present(self):
        self.assertIn("_SEARCH_EVIDENCE", self.tools_source)
        self.assertIn('"name": "search_evidence"', self.tools_source)

    def test_in_brain_and_heavy_tools(self):
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.tools_source.splitlines() if l.startswith(prefix))
            self.assertIn("_SEARCH_EVIDENCE", line)

    def test_dispatch_present(self):
        self.assertIn('if name == "search_evidence":', self.tools_source)
        self.assertIn("pubmed.search(", self.tools_source)

    def test_nursing_system_cites_evidence_in_apa(self):
        self.assertIn("search_evidence", self.app_source)
        self.assertIn("APA 7th", self.app_source)

    def test_config_present(self):
        self.assertIn("PUBMED_EUTILS_URL", self.config_source)
        self.assertIn("eutils.ncbi.nlm.nih.gov", self.config_source)


if __name__ == "__main__":
    unittest.main()
