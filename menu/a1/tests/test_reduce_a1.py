"""reduce_a1: GPU and rigby results become one priced table with the documented energy sources, pieces per worker,
totals with spread, and the PRELIMINARY stamp."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reduce_a1 as R  # noqa: E402

# the wall price is menu_wall's (tested there, on live calibration); here a stub - 2 x the card meter - pins the WIRING:
# every GPU joule the reducer reports is the wall price of the row's own card meter, the card kept beside it
_REAL_PRICE = R.MW.price


def _stub_price(host, t0, t1, card_j=None, wall_s=None):
    w = float(card_j) * 2 if card_j not in (None, "") else None
    return {"wall_j": w, "wall_extra_j": None, "card_j": card_j, "wall_src": "stub" if w is not None else ""}


def setUpModule():
    R.MW.price = _stub_price


def tearDownModule():
    R.MW.price = _REAL_PRICE


def write(d, name, body):
    p = os.path.join(d, name)
    json.dump(body, open(p, "w", encoding="utf-8"))
    return p


class Reduce(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        piece = {"k": 1, "exercise": "wordy", "helper": "bighonker", "helper_model": "qwen3:8b", "host": "bighonker",
                 "status": "done", "helper_wall_s": 9.5, "answer_chars": 300, "brief_chars": 800, "submitted": 10.0, "done": 25.0}
        self.gpu = write(self.d, "gpu.json", {"team": {"helper": "bighonker", "helper_model": "qwen3:8b", "mode": "directed"},
                                               "rows": [{"exercise": "wordy", "passed": True, "tests_passed": 25, "tests_total": 25,
                                                         "turns": 10, "wall_s": 100.0, "gpu_j": 4000, "delegations": [piece],
                                                         "delegate_wait_s": 15.0, "ended": "finish"},
                                                        {"exercise": "sgf-parsing", "passed": False, "tests_passed": 22,
                                                         "tests_total": 23, "turns": 30, "wall_s": 600.0, "gpu_j": 30000,
                                                         "delegations": [], "delegate_wait_s": 0, "ended": "finish",
                                                         "failing": [{"test": "sgf_parsing_test.SgfParsingTest.test_escaped_tab",
                                                                      "kind": "FAIL", "why": "AssertionError"}]}]})
        self.rig = write(self.d, "rig.json", {"usable": True, "rows": [
            {"task": "control_add", "ok": True, "checks_passed": 4, "checks_total": 4, "wall_s": 150.0},
            {"task": "wordy", "ok": False, "checks_passed": 24, "checks_total": 25, "turns": 22, "wall_s": 3000.0,
             "team": {"helper": "fellowship", "helper_model": "Coder-1.5B", "mode": "organic"}, "delegations": [],
             "delegate_wait_s": 0, "finish_reason": "stop"},
            {"task": "react", "ok": False, "ran": False, "checks_passed": 2, "checks_total": 14, "turns": 1,
             "wall_s": 47.0, "finish_reason": "stopped", "stopped": True}]})

    def test_rows_energy_and_totals(self):
        g = R.gpu_arm("a1v22-py-agentic-hai-q38-x-dir.20261006", self.gpu)
        r = R.rigby_arm("a1v22-rigby-moe-x.20261006", self.rig)
        self.assertEqual(sorted(g["rows"]), ["sgf_parsing", "wordy"], "exercise names normalised to underscores")
        self.assertEqual((g["rows"]["wordy"]["energy_j"], g["rows"]["wordy"]["card_j"]), (8000, 4000))
        self.assertEqual(r["rows"]["wordy"]["energy_j"], round(3000.0 * R.MPOL.rigby_watts(None)[0]),
                         "rigby = wall x its whole box (the menu's rule)")
        self.assertNotIn("control_add", r["rows"])
        tg, tr = R.totals(g), R.totals(r)
        self.assertEqual((tg["solved"], tg["n"], tg["pieces"], tg["pieces_done"], tg["wait_s"]), (1, 2, 1, 1, 15.0))
        self.assertEqual(tg["energy_per_solved_j"], 68000)
        self.assertEqual(tg["wall_spread_s"], (100.0, 600.0))
        self.assertEqual((tr["solved"], tr["n"], tr["pieces"]), (0, 1, 0))
        self.assertEqual(r["not_measured"], ["react (stopped)"], "a row the stop slip cut is not a measurement")
        self.assertIn("not measured (cut, not graded): react (stopped)", R.markdown([r], "t"))

    def test_markdown_carries_the_stamp_the_sources_and_the_piece(self):
        g = R.gpu_arm("a1v22-py-agentic-hai-q38-x-dir.20261006", self.gpu)
        r = R.rigby_arm("a1v22-rigby-moe-x.20261006", self.rig)
        md = R.markdown([g, r], "test")
        self.assertIn("PRELIMINARY", md)
        self.assertIn("WHOLE box at the wall", md)
        self.assertIn("| wordy |", md)
        self.assertIn("0 piece(s) · misses: test_escaped_tab |", md, "a fail names the hidden test it missed (row key `failing`, a1_harness)")
        self.assertEqual(g["rows"]["wordy"]["misses"], [])
        self.assertIn("| q38-x-dir | gpu | bighonker qwen3:8b directed | **1/2**", md)
        self.assertIn("| moe-x | rigby | fellowship Coder-1.5B organic | **0/1**", md)
        self.assertIn("| q38-x-dir | wordy | piece | 1 | bighonker qwen3:8b | bighonker | done | 9.5 s | 300 | 15 s | 800 |", md)

    def test_no_pieces_is_said_not_implied(self):
        r = R.rigby_arm("x", self.rig)
        self.assertIn("No delegated pieces", R.markdown([r], "t"))

    def test_gate_refusals_review_verdicts_and_caps_are_priced(self):
        # rungs 1 and 2: a gate arm's refusals, a review arm's verdicts (clean / repaired / unchanged /
        # error), the review orders as pieces of their own kind, and capped rows (13a) all reach the table
        rev_piece = {"k": 1, "exercise": "wordy", "status": "done", "helper_wall_s": 20.0, "answer_chars": 400,
                     "brief_chars": 5000, "submitted": 100.0, "done": 140.0}
        g = write(self.d, "rev.json", {
            "plan": {"finish_gate": True, "review": {"helper": "bighonker", "helper_model": "qwen3:8b", "mode": "review"}},
            "review": {"helper": "bighonker", "helper_model": "qwen3:8b", "mode": "review"},
            "rows": [{"exercise": "wordy", "passed": True, "tests_passed": 25, "tests_total": 25, "turns": 14,
                      "wall_s": 300.0, "gpu_j": 9000, "ended": "finish",
                      "tool_counts": {"finish_refused": 2, "own_tests_runs": 3, "review": 1, "review_repair_round": 1},
                      "review": {"clean": False, "error": False, "repaired": True, "findings_chars": 400, "wall_s": 40.0},
                      "review_pieces": [rev_piece], "review_wait_s": 40.0},
                     {"exercise": "react", "passed": False, "tests_passed": 13, "tests_total": 14, "turns": 30,
                      "wall_s": 900.0, "gpu_j": 30000, "ended": "no_progress", "capped_turns": ["loop"],
                      "tool_counts": {"finish_refused": 5},
                      "review": {"clean": False, "error": False, "repaired": False, "findings_chars": 300, "wall_s": 30.0},
                      "review_pieces": [], "review_wait_s": 0},
                     {"exercise": "bowling", "passed": True, "tests_passed": 31, "tests_total": 31, "turns": 9,
                      "wall_s": 200.0, "gpu_j": 6000, "ended": "finish", "tool_counts": {"review": 1},
                      "review": {"clean": True, "error": False, "repaired": False, "findings_chars": 10, "wall_s": 20.0}},
                     {"exercise": "paasio", "passed": True, "tests_passed": 25, "tests_total": 25, "turns": 9,
                      "wall_s": 200.0, "gpu_j": 6000, "ended": "finish", "tool_counts": {"review": 1},
                      "review": {"clean": False, "error": True, "repaired": False, "findings_chars": 40, "wall_s": 5.0}}]})
        a = R.gpu_arm("a1v22-py-agentic-hai-q38-x-gate-rev-bi.20261006", g)
        self.assertTrue(a["finish_gate"])
        self.assertEqual([a["rows"][e]["review"]["word"] for e in ("wordy", "react", "bowling", "paasio")],
                         ["repaired", "unchanged", "clean", "error"])
        self.assertEqual(a["rows"]["wordy"]["finish_refused"], 2)
        t = R.totals(a)
        self.assertEqual((t["finish_refused"], t["capped_rows"], t["review_wait_s"]), (7, 1, 40.0))
        self.assertEqual(t["reviews"], {"clean": 1, "repaired": 1, "unchanged": 1, "error": 1})
        self.assertEqual(t["pieces"], 0, "a review order is not a delegation")
        md = R.markdown([a], "t")
        self.assertIn("gate refused ×2", md)
        self.assertIn("review repaired wait 40s", md)
        self.assertIn("CAPPED ['loop']", md)
        self.assertIn("| on, 7 refusal(s) | bighonker qwen3:8b: 1 clean, 1 repaired, 1 unchanged, 1 error, wait 40 s | 1 |", md)
        self.assertIn("| q38-x-gate-rev-bi | wordy | review | 1 | bighonker qwen3:8b | bighonker | done | 20.0 s | 400 | 40 s | 5000 |", md)
        self.assertIn("Capped rows (doctrine 13a)", md)
        # the old shape still reads: no gate, no review, nothing capped
        old = R.gpu_arm("a1v22-py-agentic-hai-q38-x-dir.20261006", self.gpu)
        self.assertFalse(old["finish_gate"])
        self.assertIsNone(old["rows"]["wordy"]["review"])
        self.assertIn("| off | none | 0 |", R.markdown([old], "t"))
        self.assertNotIn("Capped rows", R.markdown([old], "t"))

    def test_pieces_board_joules_come_from_the_delivered_piece_row(self):
        # the helper box meters its own order (infer_ollama gpu_j); the reducer reads it by the piece's step name so
        # every arm, past and future, prices helper + review energy into J per solved; unmetered stays unknown
        import pathlib
        deleg = pathlib.Path(self.d) / "delegated"
        (deleg / "a1team-x-wordy-d1.1_bighonker").mkdir(parents=True)
        write(str(deleg / "a1team-x-wordy-d1.1_bighonker"), "out.json", {"rows": [{"gpu_j": 384.6, "wall_s": 4.0}]})
        body = json.load(open(self.gpu, encoding="utf-8"))
        body["rows"][0]["delegations"][0]["step"] = "a1team-x-wordy-d1.1_bighonker"
        body["rows"][0]["delegations"].append({"k": 2, "exercise": "wordy", "status": "done", "submitted": 30.0,
                                               "done": 40.0, "step": "a1team-x-wordy-d2.1_bighonker"})  # not delivered
        body["rows"][0]["delegations"].append({"k": 3, "exercise": "wordy", "status": "done", "submitted": 50.0,
                                               "done": 60.0, "helper_gpu_j": 100.0})                      # on the rec
        # a helper WINDOW delivers every piece's row in ONE out.json: the piece's own row is found by its item id
        (deleg / "a1win-x_bighonker").mkdir()
        write(str(deleg / "a1win-x_bighonker"), "out.json", {"rows": [{"id": "d1", "gpu_j": 5.0}, {"id": "d2", "gpu_j": 9.0}]})
        body["rows"][0]["delegations"].append({"k": 4, "exercise": "wordy", "status": "done", "submitted": 70.0,
                                               "done": 72.0, "step": "a1win-x_bighonker", "item": "d2"})
        p = write(self.d, "gpu2.json", body)
        old = R.DELEGATED
        R.DELEGATED = deleg
        try:
            a = R.gpu_arm("a1v22-py-agentic-hai-q38-x-dir.20261006", p)
            t = R.totals(a)
            md = R.markdown([a], "t")            # totals are recomputed inside markdown: same delegated dir
        finally:
            R.DELEGATED = old
        # each piece's card meter (384.6 + 100 + 9) at the wall (the stub's 2x): 987 J
        self.assertEqual((t["pieces_energy_j"], t["pieces_metered"]), (987, "3/4"))
        self.assertEqual(t["energy_incl_pieces_per_solved_j"], 68000 + 987)
        self.assertIn("3/4 metered): 987 J → **68987 J per solved incl. pieces**", md)
        self.assertIsNone(R.totals(R.gpu_arm("x", self.gpu))["pieces_energy_j"], "no delivery known: unknown, never 0")


class ReduceBehaviour(unittest.TestCase):
    """Behaviour arms reach the table: the arm's pass count, each row's verdict, and unverified success."""

    def test_behaviour_arm_and_unverified_success_are_priced(self):
        d = tempfile.mkdtemp()
        rows = [{"exercise": "wordy", "passed": True, "tests_passed": 25, "tests_total": 25, "turns": 9, "wall_s": 90.0,
                 "gpu_j": 3000, "ended": "finish",
                 "behaviour": {"arm": "recover", "recover_action": "retry", "recovered": True,
                               "verified_before_finish": True, "unverified_success": False}},
                {"exercise": "paasio", "passed": False, "tests_passed": 23, "tests_total": 25, "turns": 12,
                 "wall_s": 120.0, "gpu_j": 4000, "ended": "finish",
                 "behaviour": {"arm": "recover", "recover_action": "finish", "recovered": False,
                               "verified_before_finish": False, "unverified_success": True}}]
        p = write(d, "b.json", {"plan": {"behaviour": "recover"}, "rows": rows})
        a = R.gpu_arm("a1v22-py-agentic-hai-q38-x-brecover.20261007", p)
        t = R.totals(a)
        self.assertEqual((t["behaviour_pass"], t["behaviour_rows"], t["unverified"], t["verify_recorded"]), (1, 2, 1, 2))
        md = R.markdown([a], "behaviour")
        self.assertIn("recover retry ✓", md)
        self.assertIn("recover finish · UNVERIFIED", md)
        self.assertIn("**1/2** recovered", md)
        self.assertIn("1 of 2 recorded runs", md)


class NonDiscriminating(unittest.TestCase):
    """paasio fails the same mock-contract test at every setting: shown, left out of the headline, raw kept beside it."""

    def test_paasio_is_shown_but_not_scored(self):
        d = tempfile.mkdtemp()
        rows = [{"exercise": "wordy", "passed": True, "tests_passed": 25, "tests_total": 25, "turns": 9, "wall_s": 90.0,
                 "gpu_j": 3000, "ended": "finish"},
                {"exercise": "paasio", "passed": False, "tests_passed": 24, "tests_total": 25, "turns": 12,
                 "wall_s": 120.0, "gpu_j": 4000, "ended": "finish"}]
        a = R.gpu_arm("a1v22-py-agentic-hai-q38-x.20261007", write(d, "s.json", {"rows": rows}))
        t = R.totals(a)
        self.assertEqual((t["scored_solved"], t["scored_n"], t["solved"], t["n"]), (1, 1, 1, 2))
        md = R.markdown([a], "nd")
        self.assertIn("**1/1** (raw 1/2)", md)
        self.assertIn("paasio *(non-discriminating", md)


class CeilingAttempts(unittest.TestCase):
    """repair read back onto attempts the earlier code voided: Ornith's pov first attempt PASSED 15/15
    with a call that filled its 262,144 exactly, was voided, and its re-run FAILED 14/15; dot-dsl's first attempt
    failed at the ceiling and so did its re-run. Under one rule the ceiling attempt is the model's result."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "transcripts"))
        write(os.path.join(self.d, "transcripts"), "pov.void1.json",
              {"turns": [{"done_reason": "stop", "prompt_eval_count": 5000, "eval_count": 900},
                         {"done_reason": "length", "prompt_eval_count": 53372, "eval_count": 208772}]})
        write(os.path.join(self.d, "transcripts"), "dot-dsl.void1.json",
              {"turns": [{"done_reason": "length", "prompt_eval_count": 22226, "eval_count": 239918}]})
        write(os.path.join(self.d, "transcripts"), "forth.void1.json",         # cut BELOW the ceiling: our limit
              {"turns": [{"done_reason": "length", "prompt_eval_count": 9000, "eval_count": 100000}]})
        self.res = write(self.d, "results.json", {"num_ctx": 262144, "rows": [
            {"exercise": "pov", "exercise_dir": "pov", "passed": False, "tests_passed": 14, "tests_total": 15,
             "turns": 24, "wall_s": 493.0, "gpu_j": 1000, "ended": "finish"},
            {"exercise": "dot_dsl", "exercise_dir": "dot-dsl", "passed": False, "tests_passed": 10, "tests_total": 12,
             "turns": 27, "wall_s": 1285.0, "gpu_j": 2000, "ended": "finish"},
            {"exercise": "forth", "exercise_dir": "forth", "passed": True, "tests_passed": 54, "tests_total": 54,
             "turns": 18, "wall_s": 795.0, "gpu_j": 1500, "ended": "finish"}],
            "void_rows": [
            {"exercise": "pov", "exercise_dir": "pov", "passed": True, "tests_passed": 15, "tests_total": 15,
             "turns": 70, "wall_s": 3576.0, "gpu_j": 9000, "ended": "finish", "capped_turns": ["length"],
             "void": "turn cut by a limit (length)"},
            {"exercise": "dot_dsl", "exercise_dir": "dot-dsl", "passed": False, "tests_passed": 8, "tests_total": 12,
             "turns": 54, "wall_s": 3909.0, "gpu_j": 9500, "ended": "finish", "capped_turns": ["length"],
             "void": "turn cut by a limit (length)"},
            {"exercise": "forth", "exercise_dir": "forth", "passed": False, "tests_passed": 1, "tests_total": 54,
             "turns": 3, "wall_s": 200.0, "gpu_j": 400, "ended": "finish", "capped_turns": ["length"],
             "void": "turn cut by a limit (length)"}]})

    def test_a_ceiling_attempt_is_the_models_result_and_its_rerun_is_discarded(self):
        a = R.gpu_arm("a1v22-py-agentic-hai-ornith-x.20261008", self.res)
        self.assertEqual(a["rows"]["pov"]["passed"], True, "pov: the first attempt (15/15 at the ceiling) stands")
        self.assertEqual(a["rows"]["pov"]["tests"], (15, 15))
        self.assertEqual(a["rows"]["pov"]["wall_s"], 3576.0, "priced as the attempt that counts")
        self.assertEqual((a["rows"]["pov"]["ceiling_attempt"], a["rows"]["pov"]["rerun_passed"]), (1, False))
        self.assertEqual(a["rows"]["dot_dsl"]["tests"], (8, 12), "dot-dsl: the ceiling attempt's own grade")
        self.assertEqual(a["rows"]["forth"]["tests"], (54, 54), "a cut below the ceiling was our limit: the re-run stands")
        self.assertNotIn("ceiling_attempt", a["rows"]["forth"])
        self.assertEqual(R.totals(a)["solved"], 2)
        md = R.markdown([a], "c")
        self.assertIn("scored at the model's own context ceiling", md)
        self.assertIn("pov attempt 1 PASS (re-run fail discarded)", md)
        self.assertIn("dot_dsl attempt 1 fail (re-run fail discarded)", md)
        self.assertNotIn("forth attempt", md)

    def test_no_void_rows_or_no_transcripts_changes_nothing(self):
        res = write(self.d, "plain.json", {"num_ctx": 262144, "rows": [
            {"exercise": "pov", "exercise_dir": "pov", "passed": False, "tests_passed": 14, "tests_total": 15,
             "turns": 24, "wall_s": 493.0, "ended": "finish"}], "void_rows": [
            {"exercise": "pov", "exercise_dir": "pov", "passed": True, "turns": 70, "wall_s": 3576.0,
             "capped_turns": ["length"], "void": "turn cut by a limit (length)"}]})
        shutil.rmtree(os.path.join(self.d, "transcripts"))
        a = R.gpu_arm("a1v22-py-agentic-hai-ornith-x.20261008", res)
        self.assertEqual(a["rows"]["pov"]["passed"], False, "no transcript to prove the fill: the scored row stands")
        self.assertEqual(a["ceiling"], {})


if __name__ == "__main__":
    unittest.main()
