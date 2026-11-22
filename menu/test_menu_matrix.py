"""menu_matrix: every measured config as one row, accepted/refused and ranked by menu_policy's OWN rules.

The load-bearing test is `rank_one_is_the_policy_winner`: the config ranked 1 on a lens for a box here must be the very row
policy.json holds for that box and lens. Two derivations of one idea drift (); this pins that there is one."""
import csv
import os
import tempfile
import unittest

import menu_matrix as M
import menu_policy as P


def row(box, model, success, n, s, j, family="coding-f2", shape="S1", status="holds", think="off", t="0.6"):
    return {"box": box, "family": family, "shape": shape, "model": model, "think": think, "temperature": t,
            "n": str(n), "success": str(success), "s_per_solve": "" if s is None else str(s),
            "j_per_solve": "" if j is None else str(j), "status": status}


STOPS = {"softserve/slow/f2/off": {"why": "AUTO stop rule (priced out): ...", "except": ["0.6/1"]}}


def build(rows, stops=STOPS):
    return M.build_matrix(rows, stops)


def pick(matrix, **kw):
    hit = [e for e in matrix if all(e[k] == v for k, v in kw.items())]
    assert len(hit) == 1, "expected one row for %r, got %d" % (kw, len(hit))
    return hit[0]


class Identity(unittest.TestCase):
    def test_the_same_model_under_an_ollama_tag_and_a_gguf_file_is_one_model(self):
        a = M.identity("qwen3.6:35b-a3b-ud-q3kxl-mtp")
        b = M.identity("Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf")
        self.assertEqual((a["model"], a["build"]), (b["model"], b["build"]))
        self.assertEqual(a["model"], "Qwen3.6 35B-A3B")

    def test_a_different_build_of_the_same_model_stays_a_different_build(self):
        self.assertNotEqual(M.identity("qwen3.6:35b-a3b")["build"], M.identity("qwen3.6:35b-a3b-ud-q3kxl-mtp")["build"])
        self.assertEqual(M.identity("qwen3.6:35b-a3b")["model"], M.identity("qwen3.6:35b-a3b-ud-q3kxl-mtp")["model"])

    def test_tablet_class_and_warm_come_out_of_the_name(self):
        i = M.identity("Qwen3.5-2B-Q4_K_M @class B (warm)")
        self.assertEqual((i["model"], i["tablet_class"], i["warm"], i["mapped"]), ("Qwen3.5 2B", "B", "yes", True))
        j = M.identity("Qwen3.5-2B-Q4_K_M @class A")
        self.assertEqual((j["tablet_class"], j["warm"]), ("A", ""))
        self.assertEqual(M.identity("qwen3:8b")["tablet_class"], "")

    def test_an_unknown_model_keeps_its_name_and_is_marked_unmapped_not_dropped(self):
        i = M.identity("brand-new:7b")
        self.assertEqual((i["model"], i["build"], i["mapped"]), ("brand-new:7b", "unmapped", False))

    def test_every_model_string_in_the_live_standings_is_mapped(self):
        if not os.path.exists(P.STANDINGS):
            self.skipTest("standings.csv not on this machine")
        rows, _sp, _sha = M.load_rows()
        unmapped = sorted({r["model"] for r in rows if not M.identity(r["model"])["mapped"]})
        self.assertEqual(unmapped, [], "add these to menu_matrix.IDENTITY: %s" % unmapped)


class Matrix(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("mothership", "qwen3.8:27b", 1.0, 100, 0.5, 120.0),
            row("mothership", "qwen3.8:27b", 0.9, 100, 0.4, 100.0, t="0.0"),
            row("bighonker", "qwen3:8b", 0.97, 100, 1.0, 60.0),
            row("bighonker", "qwen3:8b", 0.80, 100, 0.5, 30.0, think="on"),
            row("bighonker", "qwen3.5:9b", 0.96, 100, 2.0, 50.0),
            row("softserve", "slow", 0.99, 100, 9.0, 40.0),                      # stopped by the filler
            row("rigby", "Qwen2.5-7B-Instruct-Q4_K_M.gguf", 0.99, 100, 30.0, None),   # energy priced from watts
            row("fellowship", "Qwen3.5-2B-Q4_K_M @class A", 0.50, 100, 2.0, None, family="chat-turn", think="off"),
            row("bighonker", "huge", 0.0, 60, None, None, status="VOID: cannot fit (40 of 60 at the cap)"),
            row("bighonker", "qwen3.5:9b", 0.2, 100, 900.0, 5000.0, t="0.9"),    # over the interactive line
        ]
        self.m = build(self.rows)

    def test_every_input_row_is_in_the_matrix_exactly_once(self):
        self.assertEqual(len(self.m), len(self.rows))

    def test_refused_rows_are_listed_with_why_and_never_ranked(self):
        slow = pick(self.m, box="softserve", model="slow")
        self.assertFalse(slow["accepted"])
        self.assertTrue(slow["why_not"].startswith("stopped: AUTO"))
        void = pick(self.m, box="bighonker", model="huge")
        self.assertIn("VOID", void["why_not"])
        for e in (slow, void):
            self.assertEqual((e["rank_quality"], e["rank_speed"], e["rank_cost"], e["pareto"]), ("", "", "", ""))

    def test_rank_one_is_the_policy_winner(self):
        fams = P.compile_policy(self.rows, STOPS)
        checked = 0
        for fam, p in fams.items():
            for tier, homes in P.TIER_HOMES.items():
                for lens, col in M.LENS_COL.items():
                    seen_box = set()
                    for w in [e for e in p["tiers"][tier][lens] if e["role"] == "home"]:
                        if w["box"] in seen_box:
                            continue          # a box's first row is its winner; the rest are other models' own winners
                        seen_box.add(w["box"])
                        ranked = [e for e in self.m if e["accepted"] and e["family"] == fam and e["box"] == w["box"]
                                  and e["rank_" + col] == 1]
                        if lens == "least_energy" and w["j_per_solve"] is None:
                            continue
                        self.assertTrue(any((r["as_measured"], r["think"], r["temperature"]) ==
                                            (w["model"], w["think"], w["temperature"]) for r in ranked),
                                        "%s %s %s: policy holds %s, matrix rank 1 is %s" % (
                                            fam, w["box"], lens, w["model"], [r["as_measured"] for r in ranked]))
                        checked += 1
        self.assertGreater(checked, 5, "the comparison matched nothing - the join is broken")

    def test_quality_is_ranked_by_the_lower_bound_so_a_big_sample_beats_a_small_lucky_one(self):
        m = build([row("bighonker", "qwen3:8b", 1.0, 5, 1.0, 50.0), row("bighonker", "qwen3.5:9b", 0.95, 200, 1.0, 50.0)])
        small = pick(m, model="Qwen3 8B")
        big = pick(m, model="Qwen3.5 9B")
        self.assertEqual((big["rank_quality"], small["rank_quality"]), (1, 2))

    def test_fastest_lens_ranks_nothing_beyond_the_interactive_line(self):
        late = pick(self.m, box="bighonker", model="Qwen3.5 9B", temperature="0.9")
        self.assertTrue(late["accepted"])
        self.assertEqual(late["rank_speed"], "")
        self.assertNotEqual(late["rank_quality"], "")

    def test_model_best_flags_one_winner_per_lens_among_a_models_own_configs(self):
        q = [e for e in self.m if e["model"] == "Qwen3 8B"]
        self.assertEqual(len(q), 2)
        thinking_on = pick(self.m, box="bighonker", model="Qwen3 8B", think="on")
        thinking_off = pick(self.m, box="bighonker", model="Qwen3 8B", think="off")
        self.assertEqual((thinking_off["model_best_quality"], thinking_on["model_best_quality"]), ("yes", ""))
        self.assertEqual((thinking_on["model_best_speed"], thinking_off["model_best_speed"]), ("yes", ""))
        self.assertEqual((thinking_on["model_best_cost"], thinking_off["model_best_cost"]), ("yes", ""))

    def test_pareto_marks_the_trade_off_frontier_and_skips_unpriced(self):
        # 27b t0.6: better quality, slower, dearer.  27b t0.0: worse quality, faster, cheaper.  Neither dominates.
        a = pick(self.m, box="mothership", temperature="0.6")
        b = pick(self.m, box="mothership", temperature="0.0")
        self.assertEqual((a["pareto"], b["pareto"]), ("yes", "yes"))
        dom = build([row("bighonker", "qwen3:8b", 0.9, 100, 1.0, 50.0), row("bighonker", "qwen3.5:9b", 0.95, 100, 0.9, 40.0)])
        self.assertEqual(pick(dom, model="Qwen3 8B")["pareto"], "no")
        self.assertEqual(pick(dom, model="Qwen3.5 9B")["pareto"], "yes")
        self.assertEqual(pick(self.m, box="fellowship")["pareto"], "", "no joules figure - no honest 3-axis verdict")

    def test_rigby_energy_is_marked_estimated_and_priced_at_watts(self):
        r = pick(self.m, box="rigby")
        self.assertEqual(r["j_source"], "estimated")
        self.assertAlmostEqual(r["j_per_solve"], 30.0 * P.rigby_watts(r["model"])[0], places=1)
        self.assertEqual(pick(self.m, box="mothership", temperature="0.6")["j_source"], "measured")
        self.assertEqual(pick(self.m, box="fellowship")["j_source"], "")

    def test_the_fellowship_keeps_its_own_tier_and_the_tablet_class(self):
        f = pick(self.m, box="fellowship")
        self.assertEqual((f["tablet_class"], f["tiers"]), ("A", "fellowship+tablets"))
        self.assertEqual(pick(self.m, box="rigby")["tiers"], "fellowship+rigby")

    def test_the_stamps_reach_every_row_when_a_stamp_is_given(self):
        def stamp(policy):
            for p in policy.values():
                for t in p["tiers"].values():
                    for rows in t.values():
                        for e in rows:
                            e["load_s"] = 7.0
        m = M.build_matrix(self.rows, STOPS, stamp=stamp)
        self.assertTrue(all(e["load_s"] == 7.0 for e in m))
        self.assertEqual(len(m), len(self.rows))


class Output(unittest.TestCase):
    def test_csv_and_page_carry_every_row_and_say_when_a_place_has_nothing_acceptable(self):
        m = build([row("mothership", "qwen3.8:27b", 1.0, 100, 0.5, 120.0),
                   row("softserve", "slow", 0.99, 100, 9.0, 40.0)])
        with tempfile.TemporaryDirectory() as d:
            M.write_csv(m, os.path.join(d, "m.csv"))
            M.write_md(m, os.path.join(d, "m.md"), "abc", "2026-10-30 00:00")
            rows = list(csv.DictReader(open(os.path.join(d, "m.csv"), encoding="utf-8")))
            md = open(os.path.join(d, "m.md"), encoding="utf-8").read()
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0].keys()), M.COLUMNS)
        self.assertEqual({r["accepted"] for r in rows}, {"yes", "no"})
        self.assertIn("## Qwen3.8 27B", md)
        self.assertIn("not on the menu", md)
        self.assertIn("never merged into one score", md)


if __name__ == "__main__":
    unittest.main()
