"""Checks for RAG grounding + the explicit forced-docs trigger.

`app.py` pulls heavy deps not present in the test env, so app-level wiring is verified from
source (same pattern as test_websearch / test_diag). The DOCS_REQUEST regex is exercised
behaviorally by compiling the same pattern.
"""
import re
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")

# Mirror of app.DOCS_REQUEST for behavioral coverage without importing app.
DOCS_REQUEST = re.compile(
    r"\b(?:runbook|documentation|knowledge ?base)\b"
    r"|\bthe docs\b|\bthe library\b|\bin (?:the )?docs\b"
    r"|\bwhat(?:'s| is| are)? documented\b"
    r"|\brun rag\b",
    re.IGNORECASE,
)
# Mirror of app.CURRENT_INFO.
CURRENT_INFO = re.compile(
    r"\bas of\b"
    r"|\b(?:latest|newest|most recent|up[- ]?to[- ]?date)\b"
    r"(?!\s+(?:photo|picture|image|pic|upload|selfie|drawing)s?\b)"
    r"|\bthese days\b|\bnowadays\b"
    r"|\b(?:this|current) year\b"
    r"|\b20(?:2[5-9]|[3-9]\d)\b"
    r"|\b(?:just |recently )?(?:released|launched|announced)\b|\bcame out\b"
    r"|\b(?:price|cost|msrp|pricing) of\b|\bhow much (?:is|does|are|do)\b"
    r"|\b(?:latest|current|newest) version\b",
    re.IGNORECASE,
)


class ForcedDocsWiringTests(unittest.TestCase):
    def test_docs_request_defined(self):
        self.assertIn("DOCS_REQUEST = re.compile(", APP)
        self.assertIn("force_docs = bool(DOCS_REQUEST.search(t))", APP)

    def test_forced_docs_pulls_more_and_bypasses_threshold(self):
        # Grounding now unions the two corpus layers (identity + persona-shared), so the per-namespace
        # search runs over each namespace in _spaces; force_docs still bumps k and bypasses the gate.
        self.assertIn("_spaces = _grounding_namespaces(ns, persona, q.user)", APP)   # + the owner's upload library
        self.assertIn("_found = rag.search(t, k=8 if force_docs else 4, namespace=_sns)", APP)
        self.assertIn("relevant = seed if force_docs else", APP)

    def test_injection_is_authoritative(self):
        self.assertIn("Base your answer on these", APP)
        self.assertIn("CITE the source filename", APP)
        # The old permissive "ignore if not relevant" wrapper is gone.
        self.assertNotIn("Reference notes that may help (ignore if not relevant)", APP)

    def test_system_grounding_rule_present(self):
        self.assertIn("GROUND HOMELAB ANSWERS IN THE DOCS", APP)

    def test_unforced_wrapper_is_supplementary_not_refuse(self):
        # The soft (non-forced) wrapper must NOT make off-topic homelab notes the only allowed source —
        # same fix as the nursing wrapper: notes are supplementary; answer from own knowledge / web_search
        # and never refuse just because the topic isn't in the notes. (Regression guard for the GPU-query bug.)
        self.assertIn("These are SUPPLEMENTARY", APP)
        self.assertIn("do NOT refuse or hedge just because the answer isn't in them", APP)
        self.assertIn("question directly from your own knowledge", APP)
        self.assertIn("web_search yourself rather than asking permission", APP)

    def test_forced_docs_logged_in_diag(self):
        self.assertIn('diag["forced_docs"] = force_docs', APP)
        self.assertIn('rec["forced_docs"] = diag["forced_docs"]', APP)

    def test_current_info_web_grounding_wired(self):
        # Deterministic web grounding for time-sensitive questions: run the search server-side and fold the
        # live results into the USER message (salient placement next to the question, like RAG ctx) — not a
        # far-away system line and not trusting the small model to emit a web_search tool call.
        self.assertIn("CURRENT_INFO = re.compile(", APP)
        self.assertIn("import websearch", APP)
        self.assertIn("want_web = bool(CURRENT_INFO.search(t)) and not nursing_mode", APP)  # skipped for nursing
        self.assertIn("web_ctx = websearch.search(t)", APP)             # fetched server-side
        self.assertIn("config.WEB_CONTEXT_MAX_CHARS", APP)              # capped like RAG ctx
        self.assertIn("LIVE WEB SEARCH RESULTS", APP)                   # injected into user_content
        self.assertIn("if want_web and web_ctx:", APP)
        self.assertIn('diag["web_grounded"]', APP)                      # observable in the diag log
        self.assertIn('rec["web_grounded"] = diag["web_grounded"]', APP)  # ...and copied into the written record


class CurrentInfoRegexTests(unittest.TestCase):
    def test_triggers(self):
        for phrase in (
            "pros and cons of a 7900XTX vs a 9070XT vs a 5070Ti as of June 2026",
            "what's the latest nvidia gpu",
            "newest AMD cpu",
            "how much does a 5070 Ti cost",
            "best graphics card in 2026",
            "what's the current version of docker",
            "what GPUs came out recently",
        ):
            self.assertTrue(CURRENT_INFO.search(phrase), phrase)

    def test_does_not_trigger(self):
        for phrase in (
            "write me a poem about cats",
            "explain how tcp works",
            "what's 2 + 2",
            "turn off the office lights",
            "turn my latest photo into a pencil sketch please",
            "make my newest upload a watercolor",
            "use the most recent picture I sent",
        ):
            self.assertIsNone(CURRENT_INFO.search(phrase), phrase)

    def test_mirror_matches_app(self):
        # The media-noun exclusion must exist in the REAL regex too, not just this mirror.
        self.assertIn(r"(?!\s+(?:photo|picture|image|pic|upload|selfie|drawing)s?\b)", APP)


class DocsRequestRegexTests(unittest.TestCase):
    def test_triggers(self):
        for phrase in (
            "check the runbook for the GPU tile",
            "it should be in the runbook",
            "look it up in the docs",
            "what's documented about caddy?",
            "run rag in the library",
            "according to the documentation",
        ):
            self.assertTrue(DOCS_REQUEST.search(phrase), phrase)

    def test_does_not_trigger(self):
        for phrase in (
            "turn off the office lights",
            "what's the weather today",
            "write me a poem about cats",
        ):
            self.assertIsNone(DOCS_REQUEST.search(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
