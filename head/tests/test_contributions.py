"""Tests for the persistent corpus-contribution queue (P0 of the user-corpus contributions project).

The queue module is dep-free (stdlib only), so its lifecycle/persistence is exercised directly with a temp
dir + an injected writer; the admin-gated endpoints + writer wiring are verified statically (app pulls heavy
deps)."""
import shutil
import tempfile
import unittest
from pathlib import Path

import config
import contributions
import tools

APP = Path("app.py").read_text(encoding="utf-8")


class ContributionQueueTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.q = contributions.ContributionQueue(self.dir)
        self.written = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _writer(self, ns, title, content, submitter):
        self.written.append((ns, title, content, submitter))
        return "contrib-written.md"

    def test_submit_lists_pending(self):
        rec = self.q.submit("Riley", "nursing_shared", "Debrief tips", "some material")
        self.assertEqual(rec["status"], "pending")
        self.assertEqual(rec["submitter"], "Riley")
        self.assertEqual(rec["target_namespace"], "nursing_shared")
        pend = self.q.list("pending")
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["id"], rec["id"])

    def test_submit_empty_returns_none(self):
        self.assertIsNone(self.q.submit("Riley", "nursing_shared", "t", "   "))

    def test_approve_calls_writer_and_marks_approved(self):
        rec = self.q.submit("Riley", "nursing_shared", "Debrief tips", "material")
        out = self.q.approve(rec["id"], "Alex", self._writer)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(out["decided_by"], "Alex")
        self.assertEqual(out["result_filename"], "contrib-written.md")
        self.assertEqual(len(self.written), 1)
        self.assertEqual(self.written[0][0], "nursing_shared")   # writer got the right namespace
        self.assertEqual(self.written[0][3], "Riley")          # ...and the submitter
        self.assertEqual(self.q.list("pending"), [])             # no longer pending
        self.assertEqual(len(self.q.list("approved")), 1)

    def test_reject_marks_rejected_and_writes_nothing(self):
        rec = self.q.submit("Riley", "nursing_shared", "t", "m")
        out = self.q.reject(rec["id"], "Alex")
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(self.written, [])
        self.assertEqual(self.q.list("pending"), [])

    def test_approve_missing_or_already_decided_returns_none(self):
        self.assertIsNone(self.q.approve("nope", "Alex", self._writer))
        rec = self.q.submit("Riley", "nursing_shared", "t", "m")
        self.q.approve(rec["id"], "Alex", self._writer)
        self.assertIsNone(self.q.approve(rec["id"], "Alex", self._writer))   # already approved -> None
        self.assertEqual(len(self.written), 1)                               # writer not called twice

    def test_writer_failure_leaves_item_pending(self):
        rec = self.q.submit("Riley", "nursing_shared", "t", "m")

        def boom(*a):
            raise RuntimeError("disk full")

        with self.assertRaises(RuntimeError):
            self.q.approve(rec["id"], "Alex", boom)
        self.assertEqual(self.q.get(rec["id"])["status"], "pending")   # still pending, recoverable

    def test_persistence_across_reload(self):
        rec = self.q.submit("Riley", "nursing_shared", "Durable", "survives a restart")
        q2 = contributions.ContributionQueue(self.dir)   # fresh instance, same state dir
        got = q2.get(rec["id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["content"], "survives a restart")
        self.assertEqual(q2.list("pending")[0]["id"], rec["id"])

    def test_content_and_title_clipped(self):
        rec = self.q.submit("Riley", "nursing_shared", "T" * 500, "C" * 50000)
        self.assertLessEqual(len(rec["title"]), 200)
        self.assertLessEqual(len(rec["content"]), 40000)


class ContributionWiringTests(unittest.TestCase):
    def test_endpoints_admin_gated_and_wired(self):
        self.assertIn("CONTRIB = contributions.queue()", APP)   # process-wide singleton shared with tools.py
        self.assertIn('@app.get("/contributions")', APP)
        self.assertIn('@app.post("/contributions/approve")', APP)
        self.assertIn('@app.post("/contributions/reject")', APP)
        self.assertIn("def _write_contribution(", APP)
        self.assertIn("Admin only.", APP)
        # approve is wired through the queue with the real writer
        self.assertIn("CONTRIB.approve(req.id, req.user, _write_contribution)", APP)


class ContributeToolTests(unittest.TestCase):
    """The user-facing submit tool: resolves the persona-shared corpus, enqueues via the SHARED singleton
    queue (so the admin endpoints see it), and is self-serve (not admin-gated)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_queue = contributions._QUEUE
        contributions._QUEUE = contributions.ContributionQueue(self.dir)   # isolate from the real queue
        self._orig_psn = dict(config.PERSONA_SHARED_NAMESPACE)
        self._orig_rag = dict(config.RAG_NAMESPACES)
        config.PERSONA_SHARED_NAMESPACE["testpersona"] = "test_shared"
        config.RAG_NAMESPACES["test_shared"] = [self.dir]

    def tearDown(self):
        contributions._QUEUE = self._orig_queue
        config.PERSONA_SHARED_NAMESPACE.clear(); config.PERSONA_SHARED_NAMESPACE.update(self._orig_psn)
        config.RAG_NAMESPACES.clear(); config.RAG_NAMESPACES.update(self._orig_rag)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_submit_enqueues_for_admin_review(self):
        out = tools.run_tool("contribute_to_shared_corpus",
                             {"title": "Shared tip", "content": "helpful for everyone"},
                             "", user="Riley", persona="testpersona")
        self.assertIn("submitted", out.lower())
        pend = contributions.queue().list("pending")
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["submitter"], "Riley")
        self.assertEqual(pend[0]["target_namespace"], "test_shared")

    def test_empty_content_rejected(self):
        out = tools.run_tool("contribute_to_shared_corpus", {"title": "x", "content": "   "},
                             "", user="Riley", persona="testpersona")
        self.assertIn("Nothing to contribute", out)
        self.assertEqual(contributions.queue().list("pending"), [])

    def test_no_shared_corpus_is_graceful(self):
        out = tools.run_tool("contribute_to_shared_corpus", {"title": "x", "content": "y"},
                             "", user="Riley", persona="nopersona")
        self.assertIn("isn't a shared corpus", out)
        self.assertEqual(contributions.queue().list("pending"), [])

    def test_submit_tool_registered_not_admin_gated(self):
        self.assertNotIn("contribute_to_shared_corpus", tools._ADMIN_TOOLS)   # submitting is self-serve
        for tier in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("contribute_to_shared_corpus", [t["function"]["name"] for t in tier])


class ContributionUITests(unittest.TestCase):
    """The admin review UI is embedded JS/HTML (CHAT_HTML) — verified statically, matching the codebase idiom
    for the frontend. JS syntax is separately guarded by the deploy's node-check of the extracted script."""

    def test_review_surfaces_present(self):
        self.assertIn('id="reviewBtn"', APP)        # admin header button (the on-demand panel)
        self.assertIn('id="reviewPanel"', APP)
        self.assertIn('id="contribPanel"', APP)     # auto-surfacing in-chat card

    def test_review_admin_gated(self):
        self.assertIn("_rb.style.display=isAdminUser()", APP)   # Review button only for admins

    def test_review_js_wired(self):
        self.assertIn("function contribCard(", APP)
        self.assertIn("async function refreshContrib(", APP)
        self.assertIn("async function actOnContrib(", APP)
        self.assertIn("fetch('/contributions/'+act", APP)                       # approve/reject POST
        self.assertIn("/contributions?user='+encodeURIComponent(user)", APP)     # list fetch
        self.assertIn("setInterval(refreshContrib", APP)                         # auto-poll
        self.assertIn("location.hash==='#review'", APP)                          # Discord deep-link opens panel


if __name__ == "__main__":
    unittest.main()
