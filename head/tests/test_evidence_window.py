"""#37 (charter WORK item 6): the critic sees the trail steps that bear on the claims, not the first 12. A coding job's
first 12 steps were always open / status / reads - never an edit or a test - so every review was blind to the work
(the BLIND REVIEWER class). The window keeps its size; what fills it is the work (edits, test runs), where the trail
ended, and steps whose results share distinctive tokens with the answer under review - shown in trail order."""
import unittest

import deliberation


def _step(tool, result="ok", n=1):
    return {"step": n, "tool": tool, "args": "{}", "result": result, "ms": 5}


def _coding_trail():
    trail = [_step("coding_read", "file %d contents" % i, n=i + 1) for i in range(15)]
    trail.append(_step("coding_patch", "patched app.py: _is_job now reads background_seconds", n=16))
    trail += [_step("coding_read", "more reading %d" % i, n=17 + i) for i in range(5)]
    trail.append(_step("coding_test", '{"returncode": 0, "stdout": "Ran 42 tests OK"}', n=22))
    trail.append(_step("coding_read", "final look", n=23))
    return trail


class TheWindowShowsTheWork(unittest.TestCase):
    def test_the_edit_and_the_test_run_are_shown_even_after_twelve_reads(self):
        """When the critic's window cannot hold the whole trail (a deliberately tight 600 chars here), the work wins."""
        out = deliberation.evidence_block(_coding_trail(), budget_chars=600)
        self.assertIn("coding_patch", out)
        self.assertIn("coding_test", out)
        self.assertIn("Ran 42 tests OK", out)
        self.assertIn("of 23", out)
        self.assertIn("did not fit the critic's window", out)

    def test_with_room_the_whole_trail_is_shown(self):
        out = deliberation.evidence_block(_coding_trail(), budget_chars=1_000_000)
        self.assertIn("23 of 23", out)

    def test_the_end_of_the_trail_is_shown(self):
        self.assertIn("final look", deliberation.evidence_block(_coding_trail()))

    def test_steps_matching_the_answer_are_preferred(self):
        trail = [_step("search_docs", "unrelated note %d" % i, n=i + 1) for i in range(20)]
        trail[4] = _step("search_docs", "BigHonker gets the layout_C RAM on 2026-10-17", n=5)
        out = deliberation.evidence_block(trail, answer="BigHonker gets layout_C RAM on 2026-10-17.")
        self.assertIn("layout_C RAM", out)

    def test_steps_stay_in_trail_order_with_their_numbers(self):
        out = deliberation.evidence_block(_coding_trail())
        self.assertLess(out.index("[step 16] coding_patch"), out.index("[step 22] coding_test"))

    def test_a_short_trail_is_shown_whole(self):
        trail = [_step("search_docs", "r%d" % i, n=i + 1) for i in range(5)]
        out = deliberation.evidence_block(trail)
        self.assertIn("5 of 5", out)
        self.assertNotIn("omitted", out)


class TheCriticSeesTheReferenceNotes(unittest.TestCase):
    """10-16 live proof: a correct answer resting on the library excerpts HAI was handed drew 3 "unsupported"
    findings because the critic never saw those excerpts."""

    def test_the_notes_are_shown_with_their_source(self):
        out = deliberation.reference_block([{"source": "RUNBOOK-Ollama-Host-Operations.md",
                                             "text": "ALL THREE GPU hosts: ollama 0.40.5 since 2026-10-15"}])
        self.assertIn("RUNBOOK-Ollama-Host-Operations.md", out)
        self.assertIn("0.40.5", out)
        self.assertIn("IS supported", out)

    def test_the_notes_are_bounded_by_the_window_and_say_so(self):
        out = deliberation.reference_block([{"source": "d%d" % i, "text": "x" * 5000} for i in range(30)],
                                           budget_chars=12000)
        self.assertLess(len(out), 13000)
        self.assertIn("of 30", out)
        self.assertIn("did not fit the critic's window", out)

    def test_the_trail_that_leaves_the_process_is_compact_and_the_critic_keeps_the_whole(self):
        """The in-memory trail carries every call whole (args_full / result_full) for the critics; the copy sent to
        the page and to chat history is the short display form."""
        import ollama_client
        full = [{"step": 1, "tool": "get_document", "args": "{}", "result": "x" * 220,
                 "args_full": "{\"name\": \"RUNBOOK\"}", "result_full": "x" * 9000, "ms": 3}]
        out = ollama_client.compact_trail(full)
        self.assertNotIn("result_full", out[0])
        self.assertNotIn("args_full", out[0])
        self.assertEqual(out[0]["result"], "x" * 220)
        self.assertIn("x" * 9000, deliberation.evidence_block(full, budget_chars=1_000_000))

    def test_the_critics_room_comes_from_their_measured_window(self):
        import app
        import config
        room = app._critic_room_chars([config.HEAVY_MODEL], "task", "answer")
        window = app._critic_window(config.HEAVY_MODEL)
        fixed = len(deliberation.CHAT_CRITIQUE_SYSTEM) + len("task") + len("answer")
        self.assertEqual(room, (window - config.CRITIC_REPLY_RESERVE_TOKENS - fixed // config.CRITIC_CHARS_PER_TOKEN)
                         * config.CRITIC_CHARS_PER_TOKEN)
        self.assertGreater(room, 12_000 * 10, "the critic sees far more than the old 12,000-char cap")

    def test_with_room_every_note_is_shown_whole(self):
        notes = [{"source": "d%d" % i, "text": "x" * 5000} for i in range(30)]
        out = deliberation.reference_block(notes, budget_chars=1_000_000)
        self.assertIn("30 of 30", out)
        self.assertNotIn("truncated", out)

    def test_no_notes_is_said(self):
        self.assertIn("none were given", deliberation.reference_block([]))


class AListResultIsDataNotASnag(unittest.TestCase):
    def test_a_search_result_mentioning_failure_is_not_a_snag(self):
        import json
        import ollama_client
        hits = json.dumps([{"source": "RUNBOOK.md", "text": "the deploy failed with an error until the fix"}])
        self.assertIsNone(ollama_client._tool_snag(hits))
        self.assertIsNotNone(ollama_client._tool_snag('{"error": "nope"}'))
        self.assertIsNotNone(ollama_client._tool_snag("request failed: connection refused"))


if __name__ == "__main__":
    unittest.main()
