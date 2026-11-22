"""Pins for triage_rewrite.py -- the library REWRITE lane payload.

The ollama call is mocked (no live model in a unit suite); every pin is on THIS payload's
own behaviour: model-mismatch refusal, serving-model recorded from the RESPONSE, the
too-large context guard, the consecutive-failure breaker, and drafts-only output shape.
"""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import triage_rewrite as R


def _work(sections):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "work.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"sections": sections}, f)
    return d, p


def _sec(rid, text, doc="Runbook/RUNBOOK.md", heading="## Test section"):
    return {"id": rid, "doc": doc, "lines": "1-10", "heading": heading,
            "text_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "triage": {"tier": "runbook", "action": "keep"}}


def _resp(draft, model="qwen3.6:27b"):
    return {"model": model, "eval_count": 42,
            "message": {"content": json.dumps(draft)}}


GOOD = {"rewrite": "The service runs on port 8099.", "cuts": [], "notes": "no change needed"}


class TestModelResolution(unittest.TestCase):
    def test_pinned_model_cannot_borrow_a_mismatched_rows_num_ctx(self):
        # a num_ctx is calibrated to a model; a 27B order landing on a box whose triage row
        # is the 8b must refuse rather than run the 27B in an 8b-sized window
        d, p = _work([_sec("s1", "text")])
        row = {"somebox": {"model": "qwen3:8b", "num_ctx": 8192}}
        with mock.patch.object(R.json, "load", side_effect=[row]), \
             mock.patch("socket.gethostname", return_value="somebox"):
            with self.assertRaises(SystemExit) as cm:
                R.main([p, os.path.join(d, "o.json"), "--model", "qwen3.6:27b"])
        self.assertIn("calibrated", str(cm.exception))

    def test_zero_sections_refuses(self):
        d, p = _work([])
        with self.assertRaises(SystemExit) as cm:
            R.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "8192"])
        self.assertIn("zero sections", str(cm.exception))


class TestParallelShares(unittest.TestCase):
    """--items-file / --merge: the desk's tier-scoped deal hands each factory a slice; the
    payload rewrites only its slice, and the merge refuses an incomplete set."""

    def _items(self, d, ids):
        p = os.path.join(d, "t.items.json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump([{"id": i, "tier": "manual", "text_b64": ""} for i in ids], f)
        return p

    def test_items_file_filters_to_the_slice_and_renames_out(self):
        d, p = _work([_sec("s1", "alpha text"), _sec("s2", "beta text")])
        ip = self._items(d, ["s2"])
        out = os.path.join(d, "o.json")
        with mock.patch.object(R, "_chat", return_value=_resp(GOOD)), \
             mock.patch.object(R, "_assert_resident"):
            rc = R.main([p, out, "--model", "m", "--num-ctx", "8192",
                         "--items-file", ip])
        self.assertEqual(rc, 0)
        share = os.path.join(d, "o.share-t.json")
        self.assertTrue(os.path.exists(share), os.listdir(d))
        got = json.load(io.open(share, encoding="utf-8"))
        self.assertEqual([r["id"] for r in got["results"]], ["s2"])

    def test_total_marker_written_for_the_runners_convention(self):
        d, p = _work([_sec("s1", "alpha"), _sec("s2", "beta")])
        out = os.path.join(d, "o.json")
        with mock.patch.object(R, "_chat", return_value=_resp(GOOD)), \
             mock.patch.object(R, "_assert_resident"):
            R.main([p, out, "--model", "m", "--num-ctx", "8192"])
        # prog dir is removed after ship; prove the write by intercepting instead
        wrote = {}
        real_open = io.open

        def spy(path, *a, **k):
            f = real_open(path, *a, **k)
            if str(path).endswith("_total"):
                wrote["path"] = str(path)
            return f

        d2, p2 = _work([_sec("s1", "alpha"), _sec("s2", "beta"), _sec("s3", "c")])
        with mock.patch.object(R, "_chat", return_value=_resp(GOOD)), \
             mock.patch.object(R, "_assert_resident"), \
             mock.patch.object(R.io, "open", side_effect=spy):
            R.main([p2, os.path.join(d2, "o.json"), "--model", "m", "--num-ctx", "8192"])
        self.assertTrue(wrote.get("path", "").endswith("_total"),
                        "the payload must write _total beside its markers")

    def test_items_file_matching_nothing_refuses(self):
        d, p = _work([_sec("s1", "alpha")])
        ip = self._items(d, ["nope"])
        with self.assertRaises(SystemExit) as cm:
            R.main([p, os.path.join(d, "o.json"), "--model", "m", "--num-ctx", "8192",
                    "--items-file", ip])
        self.assertIn("matched 0", str(cm.exception))

    def test_merge_combines_complete_shares(self):
        d, p = _work([_sec("s1", "alpha"), _sec("s2", "beta")])
        out = os.path.join(d, "o.json")
        for name, rid in (("a", "s1"), ("b", "s2")):
            with io.open(os.path.join(d, "o.share-%s.json" % name), "w",
                         encoding="utf-8") as f:
                json.dump({"results": [{"id": rid, "rewrite": "x", "cuts": [],
                                        "notes": ""}]}, f)
        rc = R.main([p, out, "--merge"])
        self.assertEqual(rc, 0)
        got = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in got["results"]), ["s1", "s2"])
        self.assertEqual(got["drafted"], 2)

    def test_merge_refuses_an_incomplete_set(self):
        d, p = _work([_sec("s1", "alpha"), _sec("s2", "beta")])
        out = os.path.join(d, "o.json")
        with io.open(os.path.join(d, "o.share-a.json"), "w", encoding="utf-8") as f:
            json.dump({"results": [{"id": "s1", "rewrite": "x", "cuts": [],
                                    "notes": ""}]}, f)
        with self.assertRaises(SystemExit) as cm:
            R.main([p, out, "--merge"])
        self.assertIn("INCOMPLETE", str(cm.exception))


class TestDrafting(unittest.TestCase):
    def _run(self, sections, chat, out_name="o.json", breaker=3):
        d, p = _work(sections)
        out = os.path.join(d, out_name)
        with mock.patch.object(R, "_assert_resident", lambda *a, **k: None), \
             mock.patch.object(R, "_chat", chat):
            rc = R.main([p, out, "--model", "qwen3.6:27b", "--num-ctx", "65536",
                         "--breaker", str(breaker), "jobname-appended-by-runner"])
        return rc, (json.load(io.open(out, encoding="utf-8"))
                    if os.path.exists(out) else None)

    def test_serving_model_is_read_from_the_response_not_the_request(self):
        # the drill lesson: routing is a REQUEST; the row records who actually answered
        rc, got = self._run([_sec("s1", "body")],
                            lambda *a, **k: _resp(GOOD, model="qwen3:8b-actually"))
        self.assertEqual(rc, 0)
        self.assertEqual(got["results"][0]["model"], "qwen3:8b-actually")

    def test_prompt_carries_charter_triage_and_source(self):
        seen = []

        def chat(host, model, prompt, num_ctx, **kw):
            seen.append(prompt)
            return _resp(GOOD)
        self._run([_sec("s1", "UNIQUE-SOURCE-TOKEN-XYZ")], chat)
        self.assertEqual(len(seen), 1)
        self.assertIn("LIBRARY CHARTER", seen[0])
        self.assertIn("UNIQUE-SOURCE-TOKEN-XYZ", seen[0])
        self.assertIn('"tier": "runbook"', seen[0])
        self.assertIn("Never silently drop content", seen[0])
        # Obsidian forward-compat: the restructure's wikilink pass is deferred, so drafts
        # stay plain markdown with mechanically-convertible cross-refs
        self.assertIn("no wikilinks", seen[0])

    def test_too_large_section_is_a_named_row_error_not_a_call(self):
        calls = []

        def chat(*a, **k):
            calls.append(1)
            return _resp(GOOD)
        big = _sec("huge", "x" * 400000)
        rc, got = self._run([big, _sec("ok", "small")], chat)
        self.assertEqual(len(calls), 1, "the too-large section must never reach the model")
        by_id = {r["id"]: r for r in got["results"]}
        self.assertIn("too large", by_id["huge"]["error"])
        self.assertIsNotNone(by_id["ok"]["rewrite"])

    def test_breaker_stops_on_consecutive_failures(self):
        def chat(*a, **k):
            raise OSError("model gone")
        rc, got = self._run([_sec("s%d" % i, "t") for i in range(6)], chat, breaker=3)
        self.assertEqual(got["errors"], 3,
                         "3 consecutive machinery failures must stop the run, not eat all 6")
        self.assertEqual(rc, 1, "zero drafts is not a success")

    def test_progress_markers_per_attempt_and_cleaned_after_ship(self):
        # the order's progress_glob counts these markers, so they must appear as sections
        # are ATTEMPTED (errors included -- ETA stays honest) and vanish with the ship
        d, p = _work([_sec("s1", "a"), _sec("s2", "b")])
        out = os.path.join(d, "o.json")
        prog = out + ".progress"
        mid_run = []

        def chat(host, model, prompt, num_ctx, **kw):
            mid_run.append(sorted(os.listdir(prog)))
            if len(mid_run) == 2:
                raise OSError("boom")          # second section errors; still a marker
            return _resp(GOOD)
        with mock.patch.object(R, "_assert_resident", lambda *a, **k: None), \
             mock.patch.object(R, "_chat", chat):
            R.main([p, out, "--model", "m", "--num-ctx", "65536"])
        self.assertEqual(mid_run[1], ["001.done", "_total"],
                         "first section's marker + the _total denominator must exist "
                         "while the second runs")
        self.assertFalse(os.path.exists(prog),
                         "markers are a byproduct - cleaned after the ship")
        got = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(got["errors"], 1)

    def test_cuts_ride_through_with_destinations(self):
        draft = {"rewrite": "clean text",
                 "cuts": [{"text": "war story about the outage", "destination": "journal"}],
                 "notes": ""}
        rc, got = self._run([_sec("s1", "body")], lambda *a, **k: _resp(draft))
        self.assertEqual(got["results"][0]["cuts"][0]["destination"], "journal")
        self.assertEqual(got["drafted"], 1)


class TestPathStrippedInterpreter(unittest.TestCase):
    def test_sibling_import_survives_safe_path_mode(self):
        # An embedded-python ._pth builds sys.path without the script dir; `python -P`
        # strips it the same way. The payload must get past its sibling import there
        # (argparse usage, not ModuleNotFoundError).
        import subprocess
        import sys as _sys
        here = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run([_sys.executable, "-P",
                            os.path.join(here, "triage_rewrite.py")],
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn("ModuleNotFoundError", r.stderr or "")


class TestCharterTie(unittest.TestCase):
    def test_schema_forbids_silent_drop(self):
        dests = R.SCHEMA["properties"]["cuts"]["items"]["properties"]["destination"]["enum"]
        self.assertEqual(sorted(dests), ["archive", "component-manual", "journal"],
                         "every cut names a real destination; 'drop' must not be offerable")


class TestVerifiedStateWiring(unittest.TestCase):
    """#30 currency gate, payload half: the verified-state block the work file carries must reach
    the model prompt as the authoritative END block, and the anti-invention rule must carve out an
    exception for it -- else the two rules contradict and the model keeps the stale source text."""

    def test_block_is_appended_with_separator(self):
        self.assertEqual(R._vs({"verified_state": "LIVE TRUTH"}), "\n\nLIVE TRUTH")

    def test_absent_block_is_empty(self):
        self.assertEqual(R._vs({}), "")

    def test_whitespace_only_block_is_empty(self):
        self.assertEqual(R._vs({"verified_state": "   \n  "}), "")

    def test_prompt_declares_the_block_authoritative(self):
        self.assertIn("Verified current state", R.PROMPT)
        self.assertIn("OVERRIDES", R.PROMPT)

    def test_anti_invention_rule_exempts_the_verified_block(self):
        # rule 2 forbids facts "not in the source"; without this carve-out it would also forbid
        # the verified block's own facts, and the model would keep the stale text unchanged
        self.assertIn("from the section itself OR", R.PROMPT)

    def test_verified_state_reaches_the_model_prompt_at_the_end(self):
        seen = []

        def chat(host, model, prompt, num_ctx, **kw):
            seen.append(prompt)
            return _resp(GOOD)
        d, p = _work([dict(_sec("s1", "stale body"),
                           verified_state="VERIFIED: Frigate removed 2026-08-12")])
        out = os.path.join(d, "o.json")
        with mock.patch.object(R, "_assert_resident", lambda *a, **k: None), \
             mock.patch.object(R, "_chat", chat):
            R.main([p, out, "--model", "qwen3.6:27b", "--num-ctx", "65536"])
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].rstrip().endswith("VERIFIED: Frigate removed 2026-08-12"),
                        "the block must be appended at the END of the prompt, where the rule "
                        "tells the model to look for it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
