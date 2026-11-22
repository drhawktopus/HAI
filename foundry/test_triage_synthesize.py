"""Pins for triage_synthesize.py, the library restructure lane's payload.

The ollama call is mocked. Every pin is on this payload's own behaviour: task selection, the two schemas, the serving
model recorded from the RESPONSE, the too-large guard, the breaker, --items-file/--merge sharing, the drafts-only
output shape, and the verified-state block (the current-truth block a work file carries reaches the model prompt, and
each task's rules say the block is authoritative). It proposes STRUCTURE only: no content-cutting path exists here,
which the schema pins enforce.
"""
import io
import json
import os
import tempfile
import unittest
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import triage_synthesize as S  # noqa: E402


def _work(task, items):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "work.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"task": task, "items": items}, f)
    return d, p


def _doc_item(rid, headings, doc="Reference/Big.md", title="# Big"):
    return {"id": rid, "doc": doc, "title": title,
            "sections": [{"heading": h, "chars": 400, "lead": "lead of " + h}
                         for h in headings]}


def _domain_item(rid, docs, domain="Tablets"):
    return {"id": rid, "domain": domain,
            "docs": [{"title": t, "path": p, "lead": "about " + t} for t, p in docs]}


def _split_resp(children, model="qwen3.6:27b"):
    draft = {"child_pages": children, "parent_stub_intro": "The domain overview.",
             "notes": ""}
    return {"model": model, "eval_count": 42, "message": {"content": json.dumps(draft)}}


def _index_resp(md="# Tablets\n\nintro", model="qwen3.6:27b"):
    draft = {"index_markdown": md, "notes": ""}
    return {"model": model, "eval_count": 42, "message": {"content": json.dumps(draft)}}


CHILDREN = [{"title": "Big - part A", "section_headings": ["## A"], "rationale": "r"},
            {"title": "Big - part B", "section_headings": ["## B"], "rationale": "r"}]


class TestTaskSelection(unittest.TestCase):
    def test_no_task_refuses(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "w.json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump({"items": [_doc_item("s1", ["## A"])]}, f)
        with self.assertRaises(SystemExit) as cm:
            S.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "8192"])
        self.assertIn("no task", str(cm.exception))

    def test_task_comes_from_work_file_when_flag_absent(self):
        d, p = _work("split-map", [_doc_item("s1", ["## A", "## B"])])
        seen = {}

        def chat(host, model, prompt, num_ctx, **kw):
            seen["schema"] = kw.get("schema")
            return _split_resp(CHILDREN)
        with mock.patch.object(S, "_assert_resident"), \
             mock.patch.object(S, "_chat", chat):
            rc = S.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "65536"])
        self.assertEqual(rc, 0)
        self.assertIs(seen["schema"], S.SPLIT_SCHEMA)

    def test_zero_items_refuses(self):
        d, p = _work("index", [])
        with self.assertRaises(SystemExit) as cm:
            S.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "8192"])
        self.assertIn("zero items", str(cm.exception))


class TestSplitMap(unittest.TestCase):
    def _run(self, items, chat, breaker=3):
        d, p = _work("split-map", items)
        out = os.path.join(d, "o.json")
        with mock.patch.object(S, "_assert_resident", lambda *a, **k: None), \
             mock.patch.object(S, "_chat", chat):
            rc = S.main([p, out, "--model", "qwen3.6:27b", "--num-ctx", "65536",
                         "--breaker", str(breaker), "jobname-from-runner"])
        return rc, (json.load(io.open(out, encoding="utf-8"))
                    if os.path.exists(out) else None)

    def test_child_pages_and_stub_ride_through(self):
        rc, got = self._run([_doc_item("s1", ["## A", "## B"])],
                            lambda *a, **k: _split_resp(CHILDREN))
        self.assertEqual(rc, 0)
        row = got["results"][0]
        self.assertEqual([c["title"] for c in row["child_pages"]],
                         ["Big - part A", "Big - part B"])
        self.assertEqual(row["parent_stub_intro"], "The domain overview.")
        self.assertEqual(row["doc"], "Reference/Big.md")

    def test_prompt_carries_headings_and_forbids_invention(self):
        seen = []

        def chat(host, model, prompt, num_ctx, **kw):
            seen.append(prompt)
            return _split_resp(CHILDREN)
        self._run([_doc_item("s1", ["## UNIQUE-HEADING-QQ"])], chat)
        self.assertIn("## UNIQUE-HEADING-QQ", seen[0])
        self.assertIn("never invent", seen[0])
        self.assertIn("retrieval key", seen[0])

    def test_serving_model_is_read_from_the_response(self):
        rc, got = self._run([_doc_item("s1", ["## A"])],
                            lambda *a, **k: _split_resp(CHILDREN, model="qwen3:8b-actually"))
        self.assertEqual(got["results"][0]["model"], "qwen3:8b-actually")


class TestIndex(unittest.TestCase):
    def test_index_markdown_rides_through(self):
        d, p = _work("index", [_domain_item("tablets",
                                            [("Cluster INDEX", "Reference/Tablet-Cluster-INDEX.md")])])
        out = os.path.join(d, "o.json")
        with mock.patch.object(S, "_assert_resident"), \
             mock.patch.object(S, "_chat", lambda *a, **k: _index_resp("# Tablets\n\nhi")):
            rc = S.main([p, out, "--task", "index", "--model", "m", "--num-ctx", "65536"])
        self.assertEqual(rc, 0)
        got = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(got["results"][0]["index_markdown"], "# Tablets\n\nhi")

    def test_index_prompt_forbids_wikilinks_and_invention(self):
        seen = []

        def chat(host, model, prompt, num_ctx, **kw):
            seen.append(prompt)
            return _index_resp()
        d, p = _work("index", [_domain_item("t", [("Doc One", "Reference/One.md")])])
        with mock.patch.object(S, "_assert_resident"), \
             mock.patch.object(S, "_chat", chat):
            S.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "65536"])
        self.assertIn("no wikilinks", seen[0])
        self.assertIn("see <FILE>", seen[0])
        self.assertIn("do not invent pages", seen[0])
        self.assertIn("Reference/One.md", seen[0])


class TestGuards(unittest.TestCase):
    def test_too_large_item_is_a_named_row_error(self):
        # a doc with hundreds of sections builds a prompt past the window; the leads are
        # truncated in the prompt (so one giant lead can't inflate it), so the real
        # too-large case is section COUNT, and it must be a named row error, not a call
        calls = []

        def chat(*a, **k):
            calls.append(1)
            return _split_resp(CHILDREN)
        big = _doc_item("huge", ["## Section number %d here" % i for i in range(400)])
        d, p = _work("split-map", [big, _doc_item("ok", ["## A"])])
        out = os.path.join(d, "o.json")
        with mock.patch.object(S, "_assert_resident"), mock.patch.object(S, "_chat", chat):
            S.main([p, out, "--model", "m", "--num-ctx", "4096"])
        self.assertEqual(len(calls), 1, "the too-large item must never reach the model")
        got = json.load(io.open(out, encoding="utf-8"))
        by = {r["id"]: r for r in got["results"]}
        self.assertIn("too large", by["huge"]["error"])
        self.assertIsNotNone(by["ok"].get("child_pages"))

    def test_breaker_stops_on_consecutive_failures(self):
        def chat(*a, **k):
            raise OSError("model gone")
        d, p = _work("split-map", [_doc_item("s%d" % i, ["## A"]) for i in range(6)])
        out = os.path.join(d, "o.json")
        with mock.patch.object(S, "_assert_resident"), mock.patch.object(S, "_chat", chat):
            rc = S.main([p, out, "--model", "m", "--num-ctx", "65536", "--breaker", "3"])
        got = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(got["errors"], 3)
        self.assertEqual(rc, 1)


class TestParallelShares(unittest.TestCase):
    def _items(self, d, ids):
        p = os.path.join(d, "t.items.json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in ids], f)
        return p

    def test_items_file_filters_and_renames(self):
        d, p = _work("split-map", [_doc_item("s1", ["## A"]), _doc_item("s2", ["## B"])])
        ip = self._items(d, ["s2"])
        out = os.path.join(d, "o.json")
        with mock.patch.object(S, "_assert_resident"), \
             mock.patch.object(S, "_chat", lambda *a, **k: _split_resp(CHILDREN)):
            rc = S.main([p, out, "--model", "m", "--num-ctx", "65536", "--items-file", ip])
        self.assertEqual(rc, 0)
        share = os.path.join(d, "o.share-t.json")
        self.assertTrue(os.path.exists(share), os.listdir(d))
        got = json.load(io.open(share, encoding="utf-8"))
        self.assertEqual([r["id"] for r in got["results"]], ["s2"])

    def test_items_file_matching_nothing_refuses(self):
        d, p = _work("split-map", [_doc_item("s1", ["## A"])])
        ip = self._items(d, ["nope"])
        with self.assertRaises(SystemExit) as cm:
            S.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "65536",
                    "--items-file", ip])
        self.assertIn("matched 0", str(cm.exception))

    def test_merge_refuses_incomplete(self):
        d, p = _work("split-map", [_doc_item("s1", ["## A"]), _doc_item("s2", ["## B"])])
        out = os.path.join(d, "o.json")
        with io.open(os.path.join(d, "o.share-a.json"), "w", encoding="utf-8") as f:
            json.dump({"results": [{"id": "s1", "child_pages": []}]}, f)
        with self.assertRaises(SystemExit) as cm:
            S.main([p, out, "--merge"])
        self.assertIn("INCOMPLETE", str(cm.exception))

    def test_merge_combines_complete(self):
        d, p = _work("split-map", [_doc_item("s1", ["## A"]), _doc_item("s2", ["## B"])])
        out = os.path.join(d, "o.json")
        for name, rid in (("a", "s1"), ("b", "s2")):
            with io.open(os.path.join(d, "o.share-%s.json" % name), "w",
                         encoding="utf-8") as f:
                json.dump({"results": [{"id": rid, "child_pages": []}]}, f)
        rc = S.main([p, out, "--merge"])
        self.assertEqual(rc, 0)
        got = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in got["results"]), ["s1", "s2"])


class TestStructureOnly(unittest.TestCase):
    def test_neither_schema_can_emit_rewritten_content(self):
        # the split path must never carry a content 'rewrite'/'cuts' field -- structure only,
        # so no byte-cutting can ever come from the model
        for schema in (S.SPLIT_SCHEMA, S.INDEX_SCHEMA):
            props = set(schema["properties"])
            self.assertNotIn("rewrite", props)
            self.assertNotIn("cuts", props)


class TestVerifiedStateBlock(unittest.TestCase):
    def test_block_is_appended_with_separator(self):
        self.assertEqual(S._vs({"verified_state": "LIVE TRUTH"}), "\n\nLIVE TRUTH")

    def test_absent_block_is_empty(self):
        self.assertEqual(S._vs({}), "")

    def test_whitespace_only_block_is_empty(self):
        self.assertEqual(S._vs({"verified_state": "   \n  "}), "")

    def test_every_task_prompt_carries_the_authoritative_rule(self):
        for p in (S.SPLIT_PROMPT, S.INDEX_PROMPT, S.RESTRUCTURE_PROMPT):
            self.assertIn("Verified current state", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
