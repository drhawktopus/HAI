import unittest

import review_context

_DIFF = (
    "diff --git a/cancellation.py b/cancellation.py\n"
    "--- a/cancellation.py\n+++ b/cancellation.py\n"
    "@@ -10,6 +10,9 @@ class QueryRunRegistry:\n"
    "+    def purge_retired(self, retention_sec=None):\n"
    "+        return 0\n"
    "-    def __old_helper__(self):\n"
    "+    def test_not_a_symbol(self):\n"
    "diff --git a/tests/test_new.py b/tests/test_new.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n+++ b/tests/test_new.py\n"
    "+def test_something():\n+    pass\n"
    "diff --git a/gone.py b//dev/null\n"
)


class ParsingTests(unittest.TestCase):
    def test_changed_files_bside_deduped_no_devnull(self):
        self.assertEqual(review_context.changed_files(_DIFF),
                         ["cancellation.py", "tests/test_new.py"])

    def test_changed_symbols_skips_tests_and_dunders(self):
        syms = review_context.changed_symbols(_DIFF)
        self.assertIn("purge_retired", syms)
        self.assertIn("QueryRunRegistry", syms)      # hunk-header context symbol
        self.assertNotIn("test_something", syms)
        self.assertNotIn("test_not_a_symbol", syms)
        self.assertNotIn("__old_helper__", syms)

    def test_symbol_cap(self):
        diff = "\n".join(f"+def fn_{i}(x):" for i in range(20))
        self.assertEqual(len(review_context.changed_symbols(diff, cap=5)), 5)


class PacketTests(unittest.TestCase):
    def _packet(self, **kw):
        reads = {"cancellation.py": "class QueryRunRegistry:\n    def purge_retired(self): ...\n",
                 "tests/test_new.py": "def test_something(): pass\n"}
        refs = {"purge_retired": ["app.py:12: registry.purge_retired()",
                                  "cancellation.py:99: self.purge_retired()"],
                "QueryRunRegistry": []}
        return review_context.build_packet(
            _DIFF, read_file=lambda p: reads.get(p), find_refs=lambda s: refs.get(s, []), **kw)

    def test_packet_contains_files_and_external_refs_only(self):
        p = self._packet()
        self.assertIn("POST-CHANGE FILE: cancellation.py", p)
        self.assertIn("app.py:12: registry.purge_retired()", p)          # external call site kept
        self.assertNotIn("cancellation.py:99", p)                        # shown-file ref dropped
        self.assertIn("REVIEW CONTEXT", p)

    def test_failed_reads_and_searches_degrade_gracefully(self):
        def boom(_):
            raise RuntimeError("runner down")
        p = review_context.build_packet(_DIFF, read_file=boom, find_refs=boom)
        self.assertEqual(p, "")                                          # nothing usable -> empty

    def test_total_budget_clips_with_note(self):
        p = self._packet(max_total=200)
        self.assertLessEqual(len(p), 400)
        self.assertIn("truncated", p)

    def test_per_file_budget_clips(self):
        big = {"cancellation.py": "x" * 50_000}
        p = review_context.build_packet(
            "diff --git a/cancellation.py b/cancellation.py\n+def f(a):\n",
            read_file=lambda q: big.get(q), find_refs=lambda s: [],
            max_file_chars=1000)
        self.assertIn("truncated to 1000", p)

    def test_empty_diff_is_empty_packet(self):
        self.assertEqual(review_context.build_packet(
            "", read_file=lambda p: "x", find_refs=lambda s: []), "")


class SeedPacketTests(unittest.TestCase):
    """Dr1 author seed packet: extract code symbols from an objective, front-load the relevant code."""

    def test_extract_seed_symbols(self):
        syms = review_context.extract_seed_symbols(
            "In overnight.render_run_summary extend _fmt_timings, touch energy.py, call should_fire")
        self.assertIn("energy.py", syms)                       # *.py filename
        self.assertIn("overnight.render_run_summary", syms)    # dotted module.func
        self.assertIn("_fmt_timings", syms)                    # snake_case symbol
        self.assertIn("should_fire", syms)

    def test_extract_skips_short_and_stopwords(self):
        syms = review_context.extract_seed_symbols("do the thing e.g. approved_overnight and abc")
        self.assertNotIn("approved_overnight", syms)   # stopword
        self.assertNotIn("abc", syms)                  # too short / no underscore

    def test_extract_caps(self):
        text = " ".join(f"mod_{i}.func_{i}" for i in range(20))
        self.assertLessEqual(len(review_context.extract_seed_symbols(text, cap=8)), 8)

    def test_build_seed_packet_front_loads_file_and_refs(self):
        def _refs(sym):
            return ["overnight.py:520: def _fmt_timings(t):"] if sym == "_fmt_timings" else []

        def _read(path):
            return "def _fmt_timings(t):\n    return 'x'\n" if path == "overnight.py" else None
        pkt = review_context.build_seed_packet(["_fmt_timings", "nope"], read_file=_read, find_refs=_refs)
        self.assertIn("EXISTING FILE", pkt)
        self.assertIn("overnight.py", pkt)
        self.assertIn("WHERE THE RELEVANT SYMBOLS", pkt)

    def test_build_seed_packet_empty_when_nothing_resolves(self):
        self.assertEqual(review_context.build_seed_packet(
            ["zzz"], read_file=lambda p: None, find_refs=lambda s: []), "")

    def test_build_seed_packet_multi_file_round_robin(self):
        # Dr1 #3: symbol A has many usages (a.py + noise files), symbol B lives in b.py. With a tight
        # file budget, B's file must STILL be seeded — the budget is shared fairly, not eaten by A.
        refs = {
            "sym_alpha": ["a.py:1: def sym_alpha():", "n1.py:9: sym_alpha()", "n2.py:9: sym_alpha()"],
            "sym_beta":  ["b.py:1: def sym_beta():"],
        }
        seen = {}

        def _read(path):
            seen[path] = True
            return f"# {path}\n"
        pkt = review_context.build_seed_packet(
            ["sym_alpha", "sym_beta"], read_file=_read, find_refs=lambda s: refs.get(s, []),
            max_files=2)
        self.assertIn("EXISTING FILE (touch here): a.py", pkt)   # alpha's primary file seeded
        self.assertIn("EXISTING FILE (touch here): b.py", pkt)   # beta's file survived the fair split
        self.assertNotIn("EXISTING FILE (touch here): n1.py", pkt)  # Incidental references do not displace primary files when max_files is enforced.

    def test_build_seed_packet_respects_max_files(self):
        refs = {s: [f"{s}.py:1: def {s}():"] for s in ("aa_x", "bb_x", "cc_x", "dd_x")}
        pkt = review_context.build_seed_packet(
            list(refs), read_file=lambda p: f"# {p}\n", find_refs=lambda s: refs.get(s, []),
            max_files=2)
        self.assertEqual(pkt.count("EXISTING FILE"), 2)

    def test_drive_context_wired_in_app(self):
        app_src = open("app.py", encoding="utf-8").read()
        self.assertIn("_drive_seed_context", app_src)
        self.assertIn("SELFENG_DRIVE_CONTEXT", app_src)
        self.assertIn("extract_seed_symbols", app_src)
        self.assertIn("SELFENG_DRIVE_CONTEXT_MAX_FILES", app_src)   # Dr1 #3 multi-file budget wired


class ScopeTargetTests(unittest.TestCase):

    def test_negated_clause_stripped(self):
        text = "Wire telemetry into _dispatch_lead. Do not change cancel_task."
        targets = review_context.extract_scope_targets(text)
        self.assertIn("_dispatch_lead", targets)
        self.assertNotIn("cancel_task", targets)

    def test_steady_state_clause_stripped(self):
        # "stays green" / "still pass" clauses name things the change must NOT be forced to touch.
        text = ("Extend _ov_verify to parse the count. "
                "Existing tests in test_overnight.py all still pass; the suite stays green.")
        targets = review_context.extract_scope_targets(text)
        self.assertIn("_ov_verify", targets)
        self.assertNotIn("test_overnight.py", targets)

    def test_filenames_and_dotted_refs_survive_clause_split(self):
        # The split must NOT fire on the '.' inside app.py or overnight.parse_test_count.
        text = "Call overnight.parse_test_count from app.py. The result lands in the report."
        targets = review_context.extract_scope_targets(text)
        self.assertIn("app.py", targets)
        self.assertIn("overnight.parse_test_count", targets)

    def test_empty_and_symbol_free_text(self):
        self.assertEqual(review_context.extract_scope_targets(""), [])
        self.assertEqual(review_context.extract_scope_targets("make the dashboard nicer"), [])

    def test_strip_negated_clauses_keeps_positive_clauses(self):
        out = review_context.strip_negated_clauses(
            "Add the helper to energy.py; never touch avery_dns.py\nWire it into the scheduler")
        self.assertIn("energy.py", out)
        self.assertIn("scheduler", out)
        self.assertNotIn("avery_dns.py", out)

    def test_example_clause_not_demanded(self):
        text = ("Persist the worker timings into the item record in overnight.py. "
                "Copied keys must survive the round-trip, e.g. drive_tele.")
        targets = review_context.extract_scope_targets(text)
        self.assertIn("overnight.py", targets)
        self.assertNotIn("drive_tele", targets)

    def test_such_as_clause_not_demanded(self):
        text = "Show per-item notes in the report. Include gate metadata such as scope_note entries."
        self.assertNotIn("scope_note", review_context.extract_scope_targets(text))

    def test_eg_normalization_spares_dotted_refs(self):
        # A naive "e.g" substring drop would false-match "state.get"; the word-boundary
        # normalization must leave ordinary dotted refs demanded.
        text = "Read state.get('items') inside render_run_summary."
        targets = review_context.extract_scope_targets(text)
        self.assertIn("render_run_summary", targets)
        self.assertIn("state.get", targets)

    def test_test_file_stem_not_demanded_as_symbol(self):
        # Specs referencing test files must demand the filename (for coverage), not the bare stem.
        # B1 gate checks non-test code, so test_* symbols should not appear as targets there.
        text = ("Fix rolling_mean in fixtures/report_stats.py and add regression tests in a new "
                "tests/test_fixture_report_stats.py covering the reported cases.")
        targets = review_context.extract_scope_targets(text)
        self.assertIn("test_fixture_report_stats.py", targets)
        self.assertNotIn("test_fixture_report_stats", targets)
        self.assertIn("rolling_mean", targets)
        self.assertIn("report_stats.py", targets)

    def test_dotted_test_ref_also_dropped(self):
        targets = review_context.extract_scope_targets(
            "Assert through tests.test_overnight_dm that the sender is shared.")
        self.assertNotIn("tests.test_overnight_dm", targets)


class StripDiffCommentsTests(unittest.TestCase):

    def test_comment_text_removed_code_kept(self):
        out = review_context.strip_diff_comments(
            "+    x = 1  # wires drive_tele in later\n+# drive_tele todo\n+y = 2")
        self.assertNotIn("drive_tele", out)
        self.assertIn("x = 1", out)
        self.assertIn("y = 2", out)

    def test_string_literal_kept(self):
        out = review_context.strip_diff_comments('+    timings["drive_tele"] = tele')
        self.assertIn("drive_tele", out)

    def test_hash_inside_string_kept(self):
        out = review_context.strip_diff_comments("+    url = 'http://x/#frag'  # trailing comment")
        self.assertIn("#frag", out)
        self.assertNotIn("trailing comment", out)

    def test_empty_and_none(self):
        self.assertEqual(review_context.strip_diff_comments(""), "")
        self.assertEqual(review_context.strip_diff_comments(None), "")


if __name__ == "__main__":
    unittest.main()
