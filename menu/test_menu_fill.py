"""Project 34 menu filler: arm holds, the price line, the ledger. Run: python test_menu_fill.py

Hermetic: no desk, no ollama, no ssh. The one file it reads from the world is menu_fill_hold.json itself, because the
stop list ordered is a decision and these tests pin what it stops (a changed stop list changes the numbers
below ON PURPOSE - update them in the same edit)."""
import collections
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import menu_boxstate as BS  # noqa: E402
import menu_fill as M  # noqa: E402
import menu_price as MP  # noqa: E402

BOX_MODELS = json.load(open(os.path.join(HERE, "box_models.json"), encoding="utf-8"))   # live lists, 10-03 (refresh_box_models_fixture.py)


def models_of(box):
    return {m: v["caps"] for m, v in BOX_MODELS[box].items()}


def put(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def count(cells, **want):
    """cells matching every given field (box, model, fam, think)."""
    idx = {"box": 0, "model": 1, "fam": 2, "think": 3, "t": 4, "sb": 5}
    return sum(1 for c in cells if all(c[idx[k]] == v for k, v in want.items()))


class HoldForms(unittest.TestCase):
    def test_an_arm_key_is_read_from_the_right(self):
        self.assertEqual(M.parse_arm("bighonker/qwen3.5:9b/f1/on"), ("bighonker", "qwen3.5:9b", "f1", "on"))
        self.assertEqual(M.parse_arm("bighonker/qwen3-vl:8b/f2/*"), ("bighonker", "qwen3-vl:8b", "f2", "*"))
        self.assertEqual(M.parse_arm("bighonker/aha2025/llama-joycaption:Q4_K_M/tool/*"),
                         ("bighonker", "aha2025/llama-joycaption:Q4_K_M", "tool", "*"), "a model name may contain '/'")
        self.assertEqual(M.parse_arm("rigby/Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf/f1/on")[1:], ("Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "f1", "on"))
        self.assertEqual(M.parse_arm("bighonker/qwen3.5:9b/coding-f1/on")[2], "f1", "the review pages' coding-f1 is accepted")

    def test_a_box_or_a_model_key_is_never_mistaken_for_an_arm(self):
        for key in ("bighonker", "bighonker/qwen3.5:9b", "bighonker/aha2025/llama-joycaption:Q4_K_M",
                    "bighonker/qwen3.5:9b/f9/on", "bighonker/qwen3.5:9b/f1/maybe"):
            self.assertIsNone(M.parse_arm(key), key)

    def test_the_existing_forms_still_hold(self):
        hold = {"bighonker/qwen3.5:35b": "x"}
        self.assertIsNotNone(M.cell_hold(hold, ("bighonker", "qwen3.5:35b", "f1", "off", "0.0", "1")))
        self.assertIsNotNone(M.cell_hold(hold, ("bighonker", "qwen3.5:35b", "tool", "on", "0.9", "7")), "every cell of the model")
        self.assertIsNone(M.cell_hold(hold, ("softserve", "qwen3.5:35b", "f1", "off", "0.0", "1")), "per box")
        self.assertIsNone(M.cell_hold(hold, ("bighonker", "qwen3:8b", "f1", "off", "0.0", "1")))
        # a whole-BOX hold is the caller's (it stops submissions; `missing` still counts what it would order)
        self.assertIsNone(M.cell_hold({"bighonker": "paused"}, ("bighonker", "qwen3:8b", "f1", "off", "0.0", "1")))

    def test_an_arm_hold_stops_its_family_and_thinking_and_nothing_else(self):
        hold = {"bighonker/qwen3.5:9b/f1/on": {"why": "cap"}}
        cell = lambda fam, think: ("bighonker", "qwen3.5:9b", fam, think, "0.6", "1")
        self.assertIsNotNone(M.cell_hold(hold, cell("f1", "on")))
        for fam, think in (("f1", "off"), ("f2", "on"), ("tool", "on"), ("vlm", "off")):
            self.assertIsNone(M.cell_hold(hold, cell(fam, think)), (fam, think))
        self.assertIsNone(M.cell_hold(hold, ("softserve", "qwen3.5:9b", "f1", "on", "0.6", "1")), "per box")
        self.assertIsNone(M.cell_hold(hold, ("bighonker", "qwen3:8b", "f1", "on", "0.6", "1")), "per model")

    def test_a_star_covers_both_thinking_settings_or_every_family(self):
        hold = {"bighonker/qwen3-vl:8b/f1/*": "x", "bighonker/qwen3:8b/*/on": "y"}
        for think in ("off", "on"):
            self.assertIsNotNone(M.cell_hold(hold, ("bighonker", "qwen3-vl:8b", "f1", think, "0.0", "1")))
        self.assertIsNone(M.cell_hold(hold, ("bighonker", "qwen3-vl:8b", "tool", "off", "0.0", "1")))
        for fam in ("f1", "f2", "tool"):
            self.assertIsNotNone(M.cell_hold(hold, ("bighonker", "qwen3:8b", fam, "on", "0.0", "1")))
        self.assertIsNone(M.cell_hold(hold, ("bighonker", "qwen3:8b", "f1", "off", "0.0", "1")))

    def test_except_leaves_exactly_the_named_cells_open(self):
        hold = {"bighonker/qwen3:8b/f1/on": {"why": "w", "except": ["0.6/4"]}}
        cells = [("bighonker", "qwen3:8b", "f1", "on", t, sb) for t in M.TEMPS for sb in M.SEEDS]
        open_ = [c for c in cells if M.cell_hold(hold, c) is None]
        self.assertEqual(open_, [("bighonker", "qwen3:8b", "f1", "on", "0.6", "4")])
        with self.assertRaises(ValueError):
            M.keep_cells({"except": ["0.6"]})

    def test_a_timed_hold_lapses_by_itself_and_the_arm_forms_survive_the_filter(self):
        h = {"a/b": {"why": "x", "until": "2026-10-02T10:00"}, "bighonker/qwen3.5:9b/f1/on": {"why": "w"}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hold.json")
            put(p, h)
            self.assertEqual(sorted(M.load_hold(p, now="2026-10-02T09:59")), ["a/b", "bighonker/qwen3.5:9b/f1/on"])
            self.assertEqual(sorted(M.load_hold(p, now="2026-10-02T10:00")), ["bighonker/qwen3.5:9b/f1/on"])

    def test_a_hold_that_stops_nothing_reports_zero_rather_than_passing_silently(self):
        cells = M.grid("bighonker", models_of("bighonker"))
        counts = M.hold_counts({"bighonker/qwen3.5:9b/f1/on": "x", "bighonker/no-such-model/f1/on": "y"}, cells)
        self.assertEqual(counts, {"bighonker/qwen3.5:9b/f1/on": 12, "bighonker/no-such-model/f1/on": 0})


class TheStopListOrdered20261002(unittest.TestCase):
    """The real menu_fill_hold.json, against each box's real model list: the number of grid cells each hold stops."""

    @classmethod
    def setUpClass(cls):
        cls.hold = M.load_hold(M.HOLD, now="2026-10-02T09:00")
        cls.grids = {b: M.grid(b, models_of(b)) for b in ("bighonker", "softserve", "mothership")}
        cls.grids["rigby"] = M.rigby_grid(["Qwen2.5-7B-Instruct-Q4_K_M.gguf", "Qwen2.5-14B-Instruct-Q4_K_M.gguf",
                                           "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",
                                           "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"])   # the 10-15 re-price lead

    def held(self, box):
        return [c for c in self.grids[box] if M.cell_hold(self.hold, c) is not None]

    def test_the_8gb_boxes_stop_exactly_the_cannot_fit_arms_and_the_unreproduced_8b_thinking_arms(self):
        for box in ("bighonker", "softserve"):
            held = self.held(box)
            n9 = count(held, box=box, model="qwen3.5:9b")
            nvl = count(held, box=box, model="qwen3-vl:8b")
            n8 = count(held, box=box, model="qwen3:8b")
            print("  %-10s qwen3.5:9b held %d (f1 on %d, f2 on %d) | qwen3-vl:8b held %d (f1 %d, f2 %d) | qwen3:8b held %d "
                  "(f1 on %d, f2 on %d)" % (box, n9, count(held, box=box, model="qwen3.5:9b", fam="f1", think="on"),
                                            count(held, box=box, model="qwen3.5:9b", fam="f2", think="on"), nvl,
                                            count(held, box=box, model="qwen3-vl:8b", fam="f1"),
                                            count(held, box=box, model="qwen3-vl:8b", fam="f2"), n8,
                                            count(held, box=box, model="qwen3:8b", fam="f1", think="on"),
                                            count(held, box=box, model="qwen3:8b", fam="f2", think="on")))
            self.assertEqual(count(held, box=box, model="qwen3.5:9b", fam="f1", think="on"), 12)
            self.assertEqual(count(held, box=box, model="qwen3.5:9b", fam="f2", think="on"), 12)
            self.assertEqual(count(held, box=box, model="qwen3.5:9b", fam="vlm"), 24, "1x vision: void by method")
            self.assertEqual(n9, 48, "qwen3.5:9b: f1 and f2 with thinking ON, plus 1x vision (void by method)")
            self.assertEqual(count(held, box=box, model="qwen3-vl:8b", fam="f1"), 24)
            self.assertEqual(count(held, box=box, model="qwen3-vl:8b", fam="f2"), 24)
            self.assertEqual(count(held, box=box, model="qwen3-vl:8b", fam="w1"), 24, "W1 hold (the owner, 2026-10-03)")
            self.assertEqual(nvl, 72, "qwen3-vl:8b: coding f1, f2 and W1, thinking off AND on")
            self.assertEqual((count(held, box=box, model="qwen3:8b", fam="f1", think="on"),
                              count(held, box=box, model="qwen3:8b", fam="f2", think="on")), (11, 11),
                             "12 cells per arm minus the one repeat cell")
            self.assertEqual(n8, 22)

    def test_vision_tool_and_every_thinking_off_text_cell_of_the_kept_models_stay_open(self):
        for box in ("bighonker", "softserve"):
            held = self.held(box)
            for model in ("qwen3-vl:8b",):
                for fam in ("tool", "vlm", "vlm2x"):
                    self.assertEqual(count(held, box=box, model=model, fam=fam), 0, (box, model, fam))
            for fam in ("tool", "vlm2x"):          # 1x vision is held: void by method
                self.assertEqual(count(held, box=box, model="qwen3.5:9b", fam=fam), 0, (box, "9b", fam))
            self.assertEqual(count(held, box=box, model="qwen3.5:9b", think="off"),
                             count(held, box=box, model="qwen3.5:9b", think="off", fam="vlm"),
                             "the 9B's thinking-off cells are kept, except 1x vision")
            self.assertEqual(count(held, box=box, model="qwen3:8b", think="off"), 0)
            self.assertEqual(count(held, box=box, model="qwen3:8b", fam="tool"), 0)

    def test_the_8b_thinking_arms_keep_exactly_one_open_cell_per_family_per_box(self):
        for box in ("bighonker", "softserve"):
            for fam in ("f1", "f2"):
                arm = [c for c in self.grids[box] if c[1] == "qwen3:8b" and c[2] == fam and c[3] == "on"]
                open_ = [c for c in arm if M.cell_hold(self.hold, c) is None]
                self.assertEqual(open_, [(box, "qwen3:8b", fam, "on", "0.6", "4")],
                                 "the repeat is a new seed set at the delivered cell's temperature (0.6, seed set 1)")

    def test_mothership_is_released_and_measured_as_the_top_end(self):
        # the owner lifted the whole-box Mothership hold ~14:05; Mothership is
        # measured as the lab's top end: the 3.8 at every knob, same-tier models it beats held (spec, same day)
        # no PERMANENT whole-box hold; a timed one (`until`, e.g. a window for an exclusive HAI job) is a different act
        whole = self.hold.get("mothership")
        self.assertFalse(whole is not None and not (isinstance(whole, dict) and whole.get("until")),
                         "Mothership carries a permanent whole-box hold again: %r" % (whole,))
        self.assertIn("bighonker/qwen3.5:35b", self.hold)
        for key in ("mothership/qwen3.6:27b", "mothership/qwen3:14b", "mothership/qwen3.8:27b-mtp-q4_K_M",
                    "mothership/qwen3.8-flash-next:q2k", "mothership/qwen3.8-flash-next:q2mix"):
            self.assertIn(key, self.hold)
        #... except 1x vision, void by method for the Qwen 3.5/3.6/3.8 family
        champ = self.held("mothership")
        self.assertGreater(count(champ, model="qwen3.8:27b", fam="vlm"), 0, "the champion's 1x vision cells are held")
        #...and on the owner's "don't get stuck on the menu" ruling, which stopped the rest of its refinement
        # cells; any OTHER hold on the champion is a regression
        why = [str(M.cell_hold(self.hold, c)) for c in champ if c[1] == "qwen3.8:27b"]
        other = [w for w in why if not (w.startswith("2026-10-02 the owner ('we want 2x vision')")
                                         or w.startswith("2026-10-07 the owner ('pretty much just refining"))]
        self.assertEqual(other, [], "the champion is held on nothing but 1x vision and the 10-07 refinement stop")

    def test_rigby_holds_everything_but_the_mtp_lead_thinking_off(self):
        """the owner: rigby's one-shot grid did its job (every model held); (#36 re-price) the hold
        was narrowed to let exactly the shortlisted lead through - the MTP file, thinking off, on the 1060 placement."""
        held = self.held("rigby")
        grid = self.grids["rigby"]
        mtp = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
        free = [c for c in grid if c not in held]
        print("  rigby held %d of %d; free %d" % (len(held), len(grid), len(free)))
        self.assertGreater(len(held), 0)
        self.assertTrue(all(c[1] == mtp and c[3] == "off" for c in free),
                        "only the MTP lead with thinking off is open: %r" % (free[:5],))
        self.assertEqual(count(held, model=mtp, think="off"), 0, "the re-price's cells are never held")

    def test_every_arm_hold_in_the_file_stops_something_on_its_own_grid(self):
        for box, cells in self.grids.items():
            counts = M.hold_counts(self.hold, cells)
            on_grid = {c[1] for c in cells}
            for key, n in counts.items():
                if key.startswith(("mothership/qwen3.8-flash-next:", "mothership/qwen3.5:9b")):
                    continue                    # installed after the fixture list was taken
                if key.split("/")[0] == box and key.split("/")[1] not in on_grid:
                    continue                    # a per-model hold for a model this fixture grid does not carry
                self.assertGreater(n, 0, "%s stops nothing on %s's grid" % (key, box))


class PriceLine(unittest.TestCase):
    def rows(self, box, fam, solves, model="m", **kw):
        """solves: [(task, wall_s, prompt_tokens)] -> solved rows on that box."""
        return [dict({"box": box, "fam": fam, "task": t, "model": model, "wall_s": w, "ok": True, "prompt_tokens": pt},
                     **kw) for t, w, pt in solves]

    def ceilings(self, rows, boxes=("bighonker", "softserve")):
        with mock.patch.object(M, "family_tasks", lambda fam: ["t1", "t2"]):
            return M.price_lines(rows, list(boxes), lambda b: ["f1"])

    def test_the_ceiling_is_ten_times_the_worst_per_task_cheapest_solve_on_that_box(self):
        rows = self.rows("bighonker", "f1", [("t1", 2.0, 100), ("t1", 4.0, 100), ("t2", 1.0, 100)])
        c, _ = self.ceilings(rows)
        self.assertEqual(c[("bighonker", "f1")], 30.0,
                         "worst of the per-task cheapest cost per solve: t1's arm (2.0 + 4.0) / 2 = 3.0, t2 1.0 -> x10")

    def test_one_boxs_rows_never_set_anothers_ceiling(self):
        rows = (self.rows("bighonker", "f1", [("t1", 2.0, 100), ("t2", 1.0, 100)])
                + self.rows("softserve", "f1", [("t1", 5.0, 100), ("t2", 3.0, 100)]))
        c, _ = self.ceilings(rows + self.rows("mothership", "f1", [("t1", 0.1, 100), ("t2", 0.1, 100)]),
                             boxes=("bighonker", "softserve", "mothership"))
        self.assertEqual((c[("bighonker", "f1")], c[("softserve", "f1")], c[("mothership", "f1")]), (20.0, 50.0, 1.0))

    def test_a_box_with_no_measured_solve_has_no_ceiling_not_zero(self):
        c, _ = self.ceilings(self.rows("bighonker", "f1", [("t1", 2.0, 100)]))
        self.assertIsNone(c[("softserve", "f1")])

    def test_a_cache_warm_row_does_not_set_the_reference(self):
        # t1: a cold 5.0 s read of a 100-token prompt, then the same prompt out of the cache (1 token read) in 0.5 s
        rows = self.rows("bighonker", "f1", [("t1", 5.0, 100), ("t1", 0.5, 1), ("t2", 4.0, 100), ("t2", 0.4, 4)])
        c, lines = self.ceilings(rows)
        self.assertEqual(c[("bighonker", "f1")], 50.0, "priced against the cold solves (5.0 and 4.0), not the warm 0.5")
        self.assertIn("ceiling 27.5 s -> 50.0 s", lines[0],
                      "the report names the move: with the warm rows t1's arm costs (5.0 + 0.5) / 2 = 2.75 s per solve")
        self.assertIn("warm left out 2", lines[0])

    def test_a_models_honest_short_prompt_is_not_warm_because_another_model_reads_more(self):
        # qwen3-vl spends ~1,100 tokens on an image the 9B reads in ~180: per-model cold sizes
        rows = (self.rows("bighonker", "f1", [("t1", 3.0, 1100)], model="vl")
                + self.rows("bighonker", "f1", [("t1", 0.6, 180), ("t2", 0.7, 180)], model="9b"))
        c, _ = self.ceilings(rows)
        self.assertEqual(c[("bighonker", "f1")], 7.0, "the 9B's 180-token reads are cold for the 9B")

    def test_a_solved_row_with_no_prompt_count_is_left_out_and_counted_not_assumed_cold(self):
        rows = self.rows("bighonker", "f1", [("t1", 0.2, None), ("t1", 6.0, 100), ("t2", 4.0, 100)])
        c, lines = self.ceilings(rows)
        self.assertEqual(c[("bighonker", "f1")], 60.0)
        self.assertIn("prompt count missing 1", lines[0])

    def test_controls_priced_out_and_unsolved_rows_never_set_a_price(self):
        rows = (self.rows("bighonker", "f1", [("control_add", 0.1, 100), ("t1", 3.0, 100), ("t2", 2.0, 100)])
                + self.rows("bighonker", "f1", [("t1", 0.2, 100)], priced_out=True)
                + self.rows("bighonker", "f1", [("t2", 0.3, 100)], ok=False))
        c, _ = self.ceilings(rows)
        self.assertEqual(c[("bighonker", "f1")], 30.0)

    def test_a_fast_mostly_wrong_arm_does_not_set_the_line_for_a_smart_one(self):
        # Nemotron 30B C1, thinking-off answers in 0.4 s and is right 1 time in 3; thinking-on 9 s, always right
        def r(think, wall, ok):
            return {"box": "mothership", "fam": "c1", "task": "t1", "model": "nemo", "think": think, "wall_s": wall,
                    "ok": ok, "prompt_tokens": 100}
        rows = [r("off", 0.4, True), r("off", 0.4, False), r("off", 0.4, False), r("off", 0.4, False),
                r("off", 0.4, False), r("off", 0.4, False), r("off", 0.4, False), r("off", 0.4, False),
                r("off", 0.4, False), r("off", 0.4, False)] + [r("on", 9.0, True)] * 3
        cheapest, _ = MP.reference(rows)
        self.assertAlmostEqual(cheapest[("mothership", "c1", "t1")], 4.0,
                               msg="off costs 4.0 s per solve (10 answers, 1 right), not the lucky 0.4 s")
        self.assertLess(9.0, 10 * cheapest[("mothership", "c1", "t1")], "the thinking arm is inside the 10x line")

    def test_rigbys_ceiling_uses_the_same_cold_reference_and_the_old_signature(self):
        solves = [{"box": "rigby", "fam": "f1", "task": "t1", "model": "m", "wall_s": 18.0, "ok": True, "prompt_tokens": 90},
                  {"box": "rigby", "fam": "f1", "task": "t1", "model": "m", "wall_s": 4.0, "ok": True, "prompt_tokens": 1},
                  {"box": "rigby", "fam": "f1", "task": "t2", "model": "m", "wall_s": 9.0, "ok": True, "prompt_tokens": 90}]
        cold, _ = MP.reference(solves)
        cheapest = {(k[1], k[2]): v for k, v in cold.items()}
        self.assertEqual(M.rigby_ceiling("f1", ["t1", "t2"], cheapest), 180.0)
        self.assertIsNone(M.rigby_ceiling("f1", ["nope"], cheapest))


class Classifier(unittest.TestCase):
    def test_priced_out_is_a_measurement_and_is_not_void(self):
        self.assertEqual(MP.row_status({"priced_out": True, "done_reason": "priced_out"}, 6144), "priced_out")
        self.assertEqual(MP.row_status({"finish_reason": "priced_out"}), "priced_out")
        self.assertEqual(MP.row_status({"done_reason": "length"}, 6144), "void")
        self.assertEqual(MP.row_status({"finish_reason": "length"}), "void")
        self.assertEqual(MP.row_status({"done_reason": "stop", "prompt_eval_count": 600, "eval_count": 5540}, 6144), "void",
                         "prompt + generated tokens at the context (ctx - 8)")
        self.assertEqual(MP.row_status({"done_reason": "stop", "prompt_eval_count": 600, "eval_count": 5000}, 6144), "answered")
        self.assertEqual(MP.row_status({"done_reason": "stop", "prompt_tokens": 90, "gen_tokens": 40}), "answered")

    def test_a_cut_row_that_also_reads_as_at_the_cap_is_still_priced_out(self):
        self.assertEqual(MP.row_status({"priced_out": True, "prompt_eval_count": 3000, "eval_count": 3200}, 6144), "priced_out")


class TheReducers(unittest.TestCase):
    """Run the real reducers on a delivered result holding one right answer, one cut at the price line and one capped."""

    def delivered(self, d, rows, ctx=6144):
        os.makedirs(os.path.join(d, "ord_host"), exist_ok=True)
        out = os.path.join(d, "ord_host", "out.json")
        put(out, {"host": "bighonker", "model": "m", "num_ctx": ctx, "rows": rows, "summary": {"energy": {}}})
        put(os.path.join(d, "submission.json"), {"orders": {"r1": {"out": out}}})

    def test_the_tool_reducer_keeps_priced_out_distinct_from_void_and_never_grades_a_cut_answer(self):
        import hard_bench_fleet_tv as T
        task = next(iter(T.HT.TASKS))
        gold = T.HT.TASKS[task]
        right = json.dumps({"tool": gold["gold_tool"], "arguments": gold["gold_args"]})
        rows = [{"id": task, "round": 1, "answer": right, "done_reason": "stop", "prompt_eval_count": 200, "eval_count": 20, "wall_s": 0.5},
                {"id": task, "round": 2, "answer": right, "thinking": "", "done_reason": "priced_out", "priced_out": True,
                 "prompt_eval_count": None, "eval_count": None, "wall_s": 7.4},
                {"id": task, "round": 3, "answer": "", "done_reason": "length", "prompt_eval_count": 200, "eval_count": 5936, "wall_s": 90.0}]
        with tempfile.TemporaryDirectory() as d:
            self.delivered(d, rows)
            with mock.patch.object(T, "OUT_DIR", T.pathlib.Path(d)), mock.patch.object(T, "RUNGS", {"r1": ("tool", "m", 6144)}), \
                 mock.patch("sys.stdout", io.StringIO()):
                T.reduce()
            got = read_csv(os.path.join(d, "fleet_tv_rows.csv"))
            md = read_text(os.path.join(d, "fleet_tv.md"))
        self.assertEqual([r["status"] for r in got], ["answered", "priced_out", "void"])
        self.assertEqual([r["ok"] for r in got], ["True", "False", "False"],
                         "the cut answer would have graded right: it must not be graded at all")
        self.assertEqual(got[1]["error"], "priced_out")
        self.assertIn("| 1 | 1 |", md, "one row at a limit and one priced out in the summary line")

    def test_the_coding_reducer_keeps_priced_out_distinct_from_void(self):
        saved = sys.argv
        sys.argv = [sys.argv[0]]
        try:
            import hard_bench_fleet_orders as F
        finally:
            sys.argv = saved
        task = F.POOL_INBAND[0]
        rows = [{"id": task, "round": 1, "answer": "x", "ok": False, "checks_passed": 0, "checks_total": 2, "done_reason": "stop",
                 "prompt_eval_count": 100, "eval_count": 50, "wall_s": 1.0},
                {"id": task, "round": 2, "answer": "def f(): pass", "ok": False, "done_reason": "priced_out", "priced_out": True,
                 "checks_passed": 0, "checks_total": 0, "wall_s": 19.5},
                {"id": task, "round": 3, "answer": "", "ok": False, "checks_passed": 0, "checks_total": 2,
                 "done_reason": "length", "prompt_eval_count": 100, "eval_count": 6040, "wall_s": 80.0}]
        with tempfile.TemporaryDirectory() as d:
            self.delivered(d, rows)
            with mock.patch.object(F, "OUT_DIR", F.pathlib.Path(d)), mock.patch.object(F, "RUNGS", {"r1": "m"}), \
                 mock.patch("sys.stdout", io.StringIO()):
                F.reduce()
            got = read_csv(os.path.join(d, "fleet_rows.csv"))
            md = read_text(os.path.join(d, "fleet.md"))
        self.assertEqual([r["status"] for r in got], ["answered", "priced_out", "void"])
        self.assertEqual(got[1]["error"], "priced_out")
        self.assertIn("| r1 | 3 | 1 | 1 |", md, "rows / priced out / void, side by side")


FIXTURES = os.path.join(HERE, "fixtures")      # desk records copied from State\p20-render-queue, lab addresses neutral (10.0.0.x)


def real(record_id):
    with open(os.path.join(FIXTURES, "desk_%s.json" % record_id), encoding="utf-8") as f:
        return json.load(f)


class TheLedger(unittest.TestCase):
    """The ledger reads the desk's own record of every factory order (menu_boxstate): one derivation for every GPU box."""

    def desk(self, *recs):
        return {r["order_name"]: r for r in recs}

    def test_the_real_records_say_what_the_desk_says(self):
        cancelled, claimed, done = real("000748ab95a6"), real("0b679da8acee"), real("01283fd2eabb")
        failed, stopped = real("0e2332d9f232"), real("1ed437c62590")
        self.assertEqual((cancelled["status"], cancelled["error"][:34]), ("failed", "cancelled on bighonker by operator"),
                         "the fixture is a real cancel record")
        self.assertEqual((claimed["status"], done["status"], failed["status"]), ("claimed", "done", "failed"))
        self.assertEqual(stopped["error"], "stopped by operator slip")
        self.assertEqual(BS.fate(cancelled), "dead:cancelled")
        self.assertEqual(BS.fate(claimed), "outstanding")
        self.assertEqual(BS.fate(done), "dead:retired",
                         "done with no out.json is a failed delivery (2026-10-02 long-path ships), not outstanding forever")
        self.assertEqual(BS.fate(failed), "dead:retired", "the payload exited non-zero")
        self.assertEqual(BS.fate(stopped), "outstanding", "a stop is a pause: a requeue may resume it")

    def test_classify_on_real_records(self):
        recs = [real(i) for i in ("000748ab95a6", "0b679da8acee", "01283fd2eabb", "0e2332d9f232", "1ed437c62590")]
        desk = self.desk(*recs)
        names = lambda r: [r["order_name"]]
        self.assertEqual(BS.classify(names(recs[0]), False, desk), "dead:cancelled")
        self.assertEqual(BS.classify(names(recs[0]), True, desk), "delivered", "a delivery outranks the desk")
        self.assertEqual(BS.classify(names(recs[1]), False, desk), "outstanding")
        self.assertEqual(BS.classify(names(recs[3]), False, desk), "dead:retired")
        self.assertEqual(BS.classify(names(recs[4]), False, desk), "outstanding")
        self.assertTrue(BS.stopped(names(recs[4]), False, desk))
        self.assertFalse(BS.stopped(names(recs[0]), False, desk))
        self.assertEqual(BS.classify(["no-such-order"], False, desk), "outstanding", "not on the desk yet: still on its way")
        self.assertEqual(BS.classify(names(recs[0]), False, None), "outstanding", "an unreadable desk leaves it outstanding")

    def test_a_desk_level_cancel_is_dead_and_a_pending_order_is_not(self):
        self.assertEqual(BS.fate({"status": "cancelled"}), "dead:cancelled")
        for s in ("pending", "held", "claimed", "running"):
            self.assertEqual(BS.fate({"status": s}), "outstanding", s)

    def test_the_softserve_form_of_a_name_matches_without_the_host_suffix(self):
        names = BS.candidate_names("hardbench-fleet-x.20260929-182048_softserve", "softserve")
        self.assertEqual(names, ["hardbench-fleet-x.20260929-182048_softserve", "hardbench-fleet-x.20260929-182048"])
        rec = {"order_name": "hardbench-fleet-x.20260929-182048", "status": "failed",
               "error": "cancelled on softserve by operator slip: x"}
        self.assertEqual(BS.classify(names, False, self.desk(rec)), "dead:cancelled")

    def test_read_desk_reads_real_record_files_and_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            for i in ("000748ab95a6", "0b679da8acee"):
                shutil.copy(os.path.join(FIXTURES, "desk_%s.json" % i), os.path.join(d, i + ".json"))
            put(os.path.join(d, "render.json"), {"id": "r1", "kind": "render", "status": "done"})      # not a factory order
            with open(os.path.join(d, "broken.json"), "w") as f:
                f.write("{not json")
            before = sorted(os.listdir(d))
            desk = BS.read_desk(d)
            self.assertEqual(sorted(os.listdir(d)), before, "read-only")
        self.assertEqual(sorted(desk), sorted([real("000748ab95a6")["order_name"], real("0b679da8acee")["order_name"]]))
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(BS.read_desk(empty), {}, "a readable desk with no orders is empty, not unreadable")
        with tempfile.TemporaryDirectory() as junk:
            with open(os.path.join(junk, "x.json"), "w") as f:
                f.write("{not json")
            self.assertIsNone(BS.read_desk(junk), "nothing readable at all is unreadable, never 'no orders'")
        self.assertIsNone(BS.read_desk(os.path.join(tempfile.gettempdir(), "no-such-desk-dir-xyz")))

    def test_the_newest_record_wins_when_two_share_a_name(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "a.json"), {"kind": "factory_order", "order_name": "o", "status": "failed",
                                            "error": "exited rc=1", "updated": "2026-10-01T00:00:00+00:00"})
            put(os.path.join(d, "b.json"), {"kind": "factory_order", "order_name": "o", "status": "claimed",
                                            "updated": "2026-10-02T00:00:00+00:00"})
            self.assertEqual(BS.read_desk(d)["o"]["status"], "claimed")

    def rec(self, cell, out, host="bighonker", names=("o_bighonker",)):
        return {"cell": cell, "out": out, "host": host, "names": list(names)}

    def cancelled(self, name):
        return {"order_name": name, "kind": "factory_order", "status": "failed", "error": "cancelled on x by operator slip: y"}

    def test_a_cancelled_cell_is_no_longer_outstanding_and_is_reorderable_unless_held(self):
        cell = ("bighonker", "qwen3:8b", "f1", "on", "0.6", "4")
        recs = [self.rec(cell, os.path.join(tempfile.gettempdir(), "no-such-folder-xyz", "out.json"), names=["o_bighonker"])]
        led = M.ledger(self.desk(self.cancelled("o_bighonker")), recs)
        self.assertEqual(M.cell_state(led[cell]), "dead")
        self.assertEqual(M.to_order(led[cell]), (True, 2, ""), "re-ordered as attempt 2, in a folder of its own")
        self.assertEqual(M.cell_state(M.ledger(None, recs)[cell]), "outstanding", "the old reading, for contrast")
        missing, held, _ = M.plan_cells([cell], {"bighonker/qwen3:8b/f1/on": "stopped"}, led)
        self.assertEqual((missing, held), ([], 1), "a hold wins over a re-order")

    def test_a_cell_retired_without_delivering_twice_is_parked_for_a_human_but_a_human_cancel_never_parks(self):
        cell = ("bighonker", "qwen3:8b", "f1", "on", "0.6", "4")
        gone = os.path.join(tempfile.gettempdir(), "no-such-folder-xyz", "out.json")
        two = [self.rec(cell, gone, names=["o1"]), self.rec(cell, gone, names=["o2"])]
        failed = lambda n: {"order_name": n, "kind": "factory_order", "status": "failed", "error": "exited rc=1"}
        led = M.ledger(self.desk(failed("o1"), failed("o2")), two)
        ok, _, why = M.to_order(led[cell])
        self.assertFalse(ok)
        self.assertIn("needs a human", why)
        one = M.ledger(self.desk(failed("o1")), two[:1])
        self.assertEqual(M.to_order(one[cell]), (True, 2, ""), "a first retirement is re-ordered once")
        cancelled = M.ledger(self.desk(self.cancelled("o1"), self.cancelled("o2")), two)
        self.assertEqual(M.to_order(cancelled[cell]), (True, 3, ""), "a decision is not machinery misbehaving")

    def test_an_outstanding_or_delivered_cell_is_never_reordered(self):
        cell = ("bighonker", "qwen3:8b", "f1", "on", "0.6", "4")
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, "o_bighonker", "out.json")
            os.makedirs(os.path.dirname(out))
            open(out, "w").close()
            led = M.ledger(self.desk(self.cancelled("o_bighonker")), [self.rec(cell, out)])
            self.assertEqual(M.cell_state(led[cell]), "delivered")
            self.assertFalse(M.to_order(led[cell])[0])
            claimed = {"order_name": "o_bighonker", "kind": "factory_order", "status": "claimed"}
            q = M.ledger(self.desk(claimed), [self.rec(cell, out + ".missing")])
            self.assertEqual(M.cell_state(q[cell]), "outstanding")
            self.assertFalse(M.to_order(q[cell])[0])
        finally:
            shutil.rmtree(tmp)

    def test_a_resumed_orders_second_folder_counts_as_delivered(self):
        tmp = tempfile.mkdtemp()
        try:
            first = os.path.join(tmp, "o_bighonker", "out.json")                 # the record names this and it never appeared
            second = os.path.join(tmp, "o_bighonker.20261001-080651", "out.json")
            os.makedirs(os.path.dirname(second))
            open(second, "w").close()
            rec = self.rec(("bighonker", "m", "f1", "on", "0.3", "7"), first)
            self.assertEqual(M.delivered_out(rec), second)
            led = M.ledger(self.desk(self.cancelled("o_bighonker")), [rec])
            self.assertEqual(M.cell_state(led[rec["cell"]]), "delivered", "it was resumed and delivered, not dead")
        finally:
            shutil.rmtree(tmp)

    def test_the_filler_orders_the_repeat_cell_and_nothing_else_of_a_cancelled_thinking_arm(self):
        hold = M.load_hold(M.HOLD, now="2026-10-02T09:00")
        gone = os.path.join(tempfile.gettempdir(), "no-such-folder-xyz", "out.json")
        for box in ("bighonker", "softserve"):
            cells = [c for c in M.grid(box, models_of(box)) if c[1] == "qwen3:8b" and c[3] == "on" and c[2] in ("f1", "f2")]
            recs = [self.rec(c, gone, host=box, names=["o%d" % i]) for i, c in enumerate(cells)]
            led = M.ledger(self.desk(*[self.cancelled("o%d" % i) for i in range(len(cells))]), recs)
            missing, held, parked = M.plan_cells(cells, hold, led)
            self.assertEqual([c for c, _ in missing], [(box, "qwen3:8b", f, "on", "0.6", "4") for f in ("f1", "f2")])
            self.assertEqual((held, parked), (22, []))



class CoreDepth(unittest.TestCase):
    """the owner every arm stays covered, at three cells each (greedy once, 0.6 twice = reproduces once)."""

    def test_every_arm_keeps_exactly_its_three_core_cells(self):
        cells = M.grid("bighonker", models_of("bighonker"))
        kept = M.core(cells)
        arms = {c[1:4] for c in cells}
        self.assertEqual({c[1:4] for c in kept}, arms, "no arm is dropped - only depth per arm is cut")
        per_arm = collections.Counter(c[1:4] for c in kept)
        self.assertEqual(set(per_arm.values()), {3})
        self.assertEqual({(c[4], c[5]) for c in kept}, {("0.0", "1"), ("0.6", "1"), ("0.6", "4")})


def _rows(box, model, think, fam, seed, wall, n=10, void=0, temp="0.6"):
    return [{"box": box, "model": model, "think": think, "fam": fam, "temp": temp, "seed": seed, "task": "t%d" % i,
             "wall_s": wall, "ok": i >= void, "void": i < void, "priced_out": False, "prompt_tokens": 100}
            for i in range(n)]


class AutoStopRule(unittest.TestCase):
    """The filler applies the stop rule itself as prices arrive. A cheap arm on the same box sets every task's cheapest solve at 1 s."""

    def setUp(self):
        self.ctx = mock.patch.dict(M.TESTED_CTX, {"bighonker": {"tested": 7168}}, clear=False)
        self.ctx.start()
        self.cheap = [r for fam in ("f1", "f2", "tool") for r in _rows("bighonker", "cheap", "off", fam, "1", 1.0)]

    def tearDown(self):
        self.ctx.stop()

    def test_cannot_fit_needs_the_tested_max_on_that_box(self):
        rows = self.cheap + _rows("bighonker", "tested", "on", "f1", "1", 2.0, void=3) \
            + _rows("bighonker", "untested", "on", "f1", "1", 2.0, void=3)
        stops = M.arm_stops(rows)
        self.assertIn("bighonker/tested/f1/on", stops)
        self.assertIn("cannot fit", stops["bighonker/tested/f1/on"]["why"])
        self.assertNotIn("bighonker/untested/f1/on", stops, "at the bench standard the cell re-runs once the fit is measured")
        self.assertEqual(len(M.arm_stops(self.cheap + _rows("bighonker", "tested", "on", "f1", "1", 2.0, void=2))), 0,
                         "20% at the cap is under FIT_VOID_SHARE - the kept arms of 2026-10-02 sat at <=12%")

    def test_priced_out_needs_two_families_and_a_repeat_on_that_box(self):
        slow = _rows("bighonker", "slow", "on", "f1", "1", 15.0) + _rows("bighonker", "slow", "on", "f2", "1", 15.0) \
            + _rows("bighonker", "slow", "on", "tool", "1", 2.0)
        self.assertEqual(M.arm_stops(self.cheap + slow), {}, "two families but no repeat: not yet reproduced")
        stops = M.arm_stops(self.cheap + slow + _rows("bighonker", "slow", "on", "f1", "4", 15.0))
        self.assertEqual(sorted(stops), ["bighonker/slow/f1/on", "bighonker/slow/f2/on"],
                         "both priced-out families stop; the cheap family (tool) stays")
        stops_t = M.arm_stops(self.cheap + slow + _rows("bighonker", "slow", "on", "f1", "1", 15.0, temp="0.3"))
        self.assertEqual(sorted(stops_t), sorted(stops), "a second TEMPERATURE is a fresh sampled run: it reproduces too")
        self.assertEqual(M.arm_stops(self.cheap + _rows("bighonker", "other", "on", "f1", "1", 15.0)
                                     + _rows("bighonker", "other", "on", "f1", "4", 15.0)), {},
                         "one family, however often repeated, is not two")

    def test_another_boxs_prices_never_stop_an_arm(self):
        rows = self.cheap + [dict(r, box="softserve") for r in
                             _rows("bighonker", "slow", "on", "f1", "1", 15.0) + _rows("bighonker", "slow", "on", "f2", "1", 15.0)
                             + _rows("bighonker", "slow", "on", "f1", "4", 15.0)]
        self.assertEqual(M.arm_stops(rows), {}, "softserve has no cheapest solve of its own here - nothing is priced")

    def test_cancel_reaches_only_outstanding_orders_of_auto_stopped_cells_and_only_once(self):
        cell = ("bighonker", "slow", "f1", "on", "0.6", "7")
        other = ("bighonker", "cheap", "f1", "off", "0.6", "7")
        recs = [{"cell": cell, "out": "", "names": ["a_bighonker"]}, {"cell": cell, "out": "", "names": ["b_bighonker"]},
                {"cell": other, "out": "", "names": ["c_bighonker"]}]
        desk = {"a_bighonker": {"id": "1", "order_name": "a_bighonker", "status": "claimed"},
                "b_bighonker": {"id": "2", "order_name": "b_bighonker", "status": "done"},
                "c_bighonker": {"id": "3", "order_name": "c_bighonker", "status": "pending"}}
        auto = {"bighonker/slow/f1/on": {"why": "AUTO stop rule (priced out)"}}
        sent = {}
        with mock.patch.object(M, "cancel_at_desk", return_value={"ok": True, "how": "factory control"}) as c:
            self.assertEqual(M.cancel_stopped(recs, desk, auto, sent), 1)
            self.assertEqual(M.cancel_stopped(recs, desk, auto, sent), 0, "already sent - never twice")
        self.assertEqual(c.call_args[0][0], "1", "only the collected order of the stopped arm; the finished and the "
                                                "unstopped are left alone")
        self.assertEqual(list(sent), ["a_bighonker"])


class PerSolvedPrice(unittest.TestCase):
    """the owner low quality is a price - the cost of a CORRECT answer (34-CHARTER "How results shape action").
    The cheap arm sets every task's cheapest solve at 1 s; PRICE_X is 10."""

    setUp, tearDown = AutoStopRule.setUp, AutoStopRule.tearDown

    def _cell(self, model, fam, seed, wall=1.5, solved=1):
        rows = _rows("bighonker", model, "on", fam, seed, wall)
        return [dict(r, ok=i < solved) for i, r in enumerate(rows)]

    def test_a_cheap_mostly_wrong_arm_is_priced_out_per_solved_and_not_per_answer(self):
        wrong = self._cell("wrong", "f1", "1") + self._cell("wrong", "f2", "1") + self._cell("wrong", "f1", "4")
        self.assertEqual(M.arm_stops(self.cheap + wrong, basis="answer"), {},
                         "1.5 s an answer is under 10x the 1 s cheapest - a wrong answer is never charged")
        stops = M.arm_stops(self.cheap + wrong, basis="solved")
        self.assertEqual(sorted(stops), ["bighonker/wrong/f1/on", "bighonker/wrong/f2/on"])
        self.assertIn("cost per correct answer", stops["bighonker/wrong/f1/on"]["why"])

    def test_one_slow_answer_does_not_price_out_a_task_the_median_answers_cheaply(self):
        """Preview a MEAN stopped Mothership's qwen3-vl:8b vision at 100% pass on one slow outlier."""
        cell = []
        for fam, seed in (("f1", "1"), ("f2", "1"), ("f1", "4")):
            for i in range(10):
                for k, wall in enumerate((1.0, 1.0, 60.0)):
                    cell.append({"box": "bighonker", "model": "steady", "think": "on", "fam": fam, "temp": "0.6",
                                 "seed": seed, "task": "t%d" % i, "wall_s": wall, "ok": True, "void": False,
                                 "priced_out": False, "prompt_tokens": 100})
        self.assertEqual(M.arm_stops(self.cheap + cell, basis="solved"), {})

    def test_it_only_adds_stops_never_reopens_one(self):
        """Preview excluding cap rows from the cost would have reopened Mothership's qwen3.5:9b W1."""
        slow = _rows("bighonker", "slow", "on", "f1", "1", 15.0) + _rows("bighonker", "slow", "on", "f2", "1", 15.0) \
            + _rows("bighonker", "slow", "on", "f1", "4", 15.0)
        capped = [dict(r, void=True, ok=False) for r in slow]
        for rows in (self.cheap + slow, self.cheap + capped):
            self.assertLessEqual(set(M.arm_stops(rows, basis="answer")), set(M.arm_stops(rows, basis="solved")))

    def test_the_live_basis_is_per_solved(self):
        self.assertEqual(M.PRICE_BASIS, "solved")

    def test_rigby_is_priced_per_solved_and_keeps_cannot_fit(self):
        """rigby answers one-shot like a GPU box; before this it had cannot-fit only."""
        cheap = [dict(r, box="rigby") for r in self.cheap]
        wrong = [dict(r, box="rigby") for r in
                 self._cell("wrong", "f1", "1") + self._cell("wrong", "f2", "1") + self._cell("wrong", "f1", "4")]
        good = [dict(r, box="rigby", model="good", ok=True) for r in
                self._cell("good", "f1", "1") + self._cell("good", "f2", "1") + self._cell("good", "f1", "4")]
        capped = [dict(r, box="rigby") for r in _rows("bighonker", "capped", "on", "f1", "1", 2.0, void=3)]
        stops = M.rigby_stops(cheap + wrong + good + capped)
        self.assertEqual(sorted(stops), ["rigby/capped/f1/on", "rigby/wrong/f1/on", "rigby/wrong/f2/on"])
        self.assertEqual(M.arm_stops(cheap + wrong + good + capped, judge_price=False), {"rigby/capped/f1/on":
                         stops["rigby/capped/f1/on"]}, "without per-solved, rigby had cannot-fit only")


class W1MeasuredAtFullCapability(unittest.TestCase):
    """the owner W1 runs with no per-answer price ceiling (quality first); the arm-level stop rules still judge."""

    def test_w1_on_the_frontier_box_carries_no_ceiling_even_with_history(self):
        rows = [{"box": "mothership", "fam": "w1", "task": t, "model": "m", "wall_s": 2.0, "ok": True,
                 "prompt_tokens": 500} for t in M.family_tasks("w1")]
        ceilings, _ = M.price_lines(rows, ["mothership"], lambda b: ["w1", "f2"])
        self.assertIsNone(ceilings[("mothership", "w1")])

    def test_w1_on_a_helper_box_carries_the_box_price_line(self):
        """the owner a 1.3B helper spent 46 min on one W1 answer - on the helper boxes W1 is a final-answer
        starting suggestion, priced like any family (its helper value is the S10 shapes, not this cell)."""
        for box in ("bighonker", "softserve"):
            rows = [{"box": box, "fam": "w1", "task": t, "model": "m", "wall_s": 2.0, "ok": True,
                     "prompt_tokens": 500} for t in M.family_tasks("w1")]
            ceilings, _ = M.price_lines(rows, [box], lambda b: ["w1"])
            self.assertEqual(ceilings[(box, "w1")], 20.0, box)
        self.assertEqual(M.rigby_ceiling("w1", M.family_tasks("w1")[:3],
                                         {("w1", t): 5.0 for t in M.family_tasks("w1")}), 50.0)

    def test_gpu_w1_orders_carry_the_cannot_fit_brake_and_priced_families_do_not(self):
        cmds = []

        def run(cmd, **kw):
            cmds.append(cmd)
            return mock.Mock(returncode=0, stdout='{"ok": true}', stderr="")
        with mock.patch.object(M.subprocess, "run", run):
            M.submit_cell("bighonker", "qwen3:8b", "w1", "off", "0.6", "1")
            M.submit_cell("bighonker", "qwen3:8b", "f2", "off", "0.6", "1", ceiling=120.0)
        w1, f2 = cmds
        self.assertEqual(w1[w1.index("--at-cap-brake") + 1], str(M.FIT_VOID_SHARE))
        self.assertNotIn("--at-cap-brake", f2)

    def test_other_families_keep_their_ceiling(self):
        tasks = M.family_tasks("f2")
        self.assertEqual(M.rigby_ceiling("f2", tasks[:2], {("f2", t): 3.0 for t in tasks}), 30.0)


class TheChampionIsNeverAutoStopped(unittest.TestCase):
    """the owner: the champion is priced at every knob."""

    def rows(self, model):
        return [{"box": "mothership", "model": model, "fam": "w1", "think": "on", "temp": "0.6", "seed": "1",
                 "task": "t%d" % i, "wall_s": 900.0, "ok": False, "void": True} for i in range(8)]

    def test_rows_that_stop_another_tested_model_leave_the_champion_running(self):
        self.assertIn("mothership/qwen3.6:27b/w1/on", M.arm_stops(self.rows("qwen3.6:27b")), "positive control")
        self.assertEqual(M.arm_stops(self.rows(M.CHAMPION["mothership"])), {})
        self.assertEqual(M.CHAMPION["mothership"], "qwen3.8:27b")

    def test_the_champions_w1_orders_carry_no_brake(self):
        cmds = []
        with mock.patch.object(M.subprocess, "run", lambda cmd, **kw: cmds.append(cmd) or mock.Mock(
                returncode=0, stdout='{"ok": true}', stderr="")):
            M.submit_cell("mothership", "qwen3.8:27b", "w1", "on", "0.6", "1")
            M.submit_cell("mothership", "qwen3.6:27b", "w1", "on", "0.6", "1")
        self.assertNotIn("--at-cap-brake", cmds[0])
        self.assertIn("--at-cap-brake", cmds[1], "positive control: any other model is braked")


class RigbyCannotFit(unittest.TestCase):
    """: rigby arms are judged by the same cannot-fit rule as the GPU boxes, and an
    unceiled family's order carries the rule's share so Tolkien brakes the arm mid-order (tolkien_llm.brake_verdict)."""
    MODEL = "qwen2.5-0.5b-instruct-q4_k_m.gguf"

    def rows(self, capped, total):
        return [{"box": "rigby", "model": self.MODEL, "fam": "w1", "think": "off", "temp": "0.0", "seed": "1",
                 "task": "t%d" % i, "wall_s": 9.0, "ok": False, "void": i < capped} for i in range(total)]

    def test_rigby_rows_at_the_context_stop_the_arm(self):
        stops = M.arm_stops(self.rows(2, 2))
        self.assertIn("rigby/%s/w1/off" % self.MODEL, stops)

    def test_rigby_rows_under_the_share_do_not(self):
        self.assertEqual(M.arm_stops(self.rows(1, 8)), {})

    def test_a_loop_counts_toward_cannot_fit_like_a_cap_hit(self):
        rows = [dict(r, void=False, loop=True) for r in self.rows(0, 2)]
        self.assertIn("rigby/%s/w1/off" % self.MODEL, M.arm_stops(rows))
        self.assertEqual(M.arm_stops([dict(r, void=False, loop=False) for r in self.rows(0, 2)]), {}, "control")

    def test_rigby_is_judged_by_the_cannot_fit_rule_only(self):
        rows = [dict(r, void=False, priced_out=True, temp=t, fam=f) for t in ("0.0", "0.6") for f in ("f1", "f2")
                for r in self.rows(0, 4)]
        self.assertTrue(M.arm_stops(rows), "positive control: these rows are priced out under the GPU rule")
        self.assertEqual(M.arm_stops(rows, judge_price=False), {})

    def test_at_cap_means_the_grids_context(self):
        # the live rows: 184 + 32,559 at 32,768, then prompt_tokens 1 on a cache hit, ungraded (E2BIG)
        row = {"finish_reason": "length", "prompt_tokens": 1, "gen_tokens": 32559, "ran": False,
               "error": "OSError: [Errno 7] Argument list too long"}
        self.assertTrue(M.rigby_at_cap(row, M.RIGBY_CTX))
        self.assertTrue(M.rigby_measured(row, M.RIGBY_CTX), "the model's answer, graded or not - never re-asked")
        self.assertFalse(M.rigby_at_cap(row, 2048), "a smaller order context measured the cap (13a)")
        self.assertFalse(M.rigby_measured(row, 2048))
        self.assertFalse(M.rigby_at_cap(row, None), "unknown context is not the grid's")
        self.assertFalse(M.rigby_at_cap(dict(row, finish_reason="stop"), M.RIGBY_CTX))
        self.assertTrue(M.rigby_measured({"finish_reason": "stop", "ran": True}, M.RIGBY_CTX))
        self.assertFalse(M.rigby_measured({"finish_reason": "stopped", "ran": False}, M.RIGBY_CTX))

    def test_the_orders_context_comes_from_its_result(self):
        res = {"cohort": {"arms": [{"arm": "rigby", "ctx": 32768}, {"arm": "voltron", "ctx": 4096}]}}
        self.assertEqual((M.order_ctx(res, "rigby"), M.order_ctx(res, "voltron"), M.order_ctx({}, "rigby")),
                         (32768, 4096, None))

    def submit(self, fam):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "inbox"))
        with mock.patch.object(M, "TABJOBS", d), mock.patch.object(M, "RIGBY_SUBS", os.path.join(d, "subs.json")):
            name, _ = M.rigby_submit(("rigby", self.MODEL, fam, "off", "0.0", "1"), {(M.rigby_tasks(fam)[0], 1)}, {}, {})
            with open(os.path.join(d, "inbox", name + ".tjob.json"), encoding="utf-8") as f:
                return json.load(f)

    def test_the_mtp_files_own_placement_is_its_grid_and_its_orders_carry_it(self):
        """(#36 re-pricing): the MTP file runs on the standard 1060 order (draft-mtp 2). Its rows are grid
        rows, not a speculation knob beside the grid; its orders carry the placement, never a CPU run of the file; and
        any OTHER speculation on any file stays outside the grid."""
        mtp = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
        self.assertIn(mtp, M.RIGBY_THINKING)
        row = {"model": mtp, "task": "t1", "replicate": 1, "temperature": 0.6, "reasoning": "off",
               "spec_type": "draft-mtp"}
        self.assertEqual(M.rigby_cell(row, "f1"), ("rigby", mtp, "f1", "off", "0.6", "1"))
        self.assertIsNone(M.rigby_cell(dict(row, spec_type="ngram-cache"), "f1"), "another speculation: a knob")
        self.assertIsNone(M.rigby_cell(dict(row, model=self.MODEL), "f1"), "the CPU file has no own speculation")
        self.assertIsNone(M.rigby_cell(dict(row, task="control_add"), "f1"))
        old = self.MODEL
        self.MODEL = mtp
        try:
            spec = self.submit("f2")
        finally:
            self.MODEL = old
        for k, v in M.RIGBY_PLACEMENT[mtp]["fields"].items():
            self.assertEqual(spec[k], v, k)
        self.assertIn("placement gtx1060-standard", spec["why"])
        self.assertNotIn("gpu", self.submit("f2"), "a file without a placement keeps the CPU path")

    def test_an_unceiled_order_states_the_rules_share(self):
        self.assertEqual(self.submit("w1")["at_cap_brake"], M.FIT_VOID_SHARE)
        self.assertNotIn("at_cap_brake", self.submit("f2"), "a priced family keeps its price brake only")

    def test_outstanding_orders_of_a_stopped_arm_get_one_stop_slip(self):
        import tempfile
        d = tempfile.mkdtemp()
        cell = ["rigby", self.MODEL, "w1", "off", "0.6", "1"]
        other = ["rigby", "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "w1", "off", "0.6", "1"]
        out = {"hb-a": {"cell": cell, "items": []}, "hb-b": {"cell": other, "items": []}}
        auto = {"rigby/%s/w1/off" % self.MODEL: {"why": "AUTO stop rule (cannot fit)"}}
        sent = {}
        with mock.patch.object(M, "rigby_outstanding", return_value=({}, out)):
            self.assertEqual(M.stop_rigby_stopped(auto, sent, inbox=d), 1)
            self.assertEqual(M.stop_rigby_stopped(auto, sent, inbox=d), 0, "once per order")
        self.assertEqual(os.listdir(d), ["hb-a.stop.json"])


class ChatFamilyC1(unittest.TestCase):
    """C1 (hard_tasks_c1.py, charter step 2) is wired like W1: in every text model's grid, read back from its order folders
    as c1, ordered with --pool c1, graded by its own payload - and never confused with W1's tablet lane c15b."""

    def test_every_text_model_gets_c1_cells(self):
        cells = M.grid("mothership", {"qwen3.8:27b": {"completion", "thinking"}, "qwen3-vl:8b": {"completion", "vision"}})
        self.assertEqual(count(cells, model="qwen3.8:27b", fam="c1"), count(cells, model="qwen3.8:27b", fam="w1"))
        self.assertGreater(count(cells, model="qwen3-vl:8b", fam="c1"), 0)

    def test_c1_has_its_own_grader_and_rigby_payload(self):
        self.assertEqual(M.GRADE_PAYLOAD["c1"], "map_swarm_code_hard_c1.py")
        self.assertIn("c1", M.RIGBY_FAMS)
        self.assertTrue(M.RIGBY_PAYLOAD["c1"].endswith("/map_swarm_code_hard_c1.py"))
        self.assertEqual(M.rigby_fam("hardbench-rigby-m-x-c1-t06-off-s1.results.json"), "c1")
        self.assertEqual(len(M.rigby_tasks("c1")), 20)
        self.assertEqual(M.FAM_ALIAS["chat-c1"], "c1")

    def test_c1_tokens_never_match_w1s_c15b_lane(self):
        for name in ("map_swarm_code_hard_w1_c15b.py", "hardbench-w1-c15b-cal.20261002-232402",
                     "fleet-g-mo-qwen3-coder-30b-w1-t00-off-s1"):
            self.assertFalse(M.is_c1(name), name)
        for name in ("map_swarm_code_hard_c1.py", "map_swarm_code_hard_c1_c15b.py", "fleet-g-mo-qwen3-8b-c1-t00-off-s1"):
            self.assertTrue(M.is_c1(name), name)
        self.assertEqual(M.rigby_fam("hardbench-rigby-m-x-w1-t06-off-s1.results.json"), "w1")

    def test_a_c1_order_names_its_pool(self):
        cmds = []
        with mock.patch.object(M.subprocess, "run", lambda cmd, **kw: cmds.append(cmd) or mock.Mock(
                returncode=0, stdout='{"ok": true}', stderr="")):
            M.submit_cell("bighonker", "qwen3:8b", "c1", "off", "0.6", "1")
        self.assertEqual(cmds[0][cmds[0].index("--pool") + 1], "c1")
        self.assertIn("hard_bench_fleet_orders.py", " ".join(cmds[0]))


class WorkFamilyW1(unittest.TestCase):
    """W1 (hard_tasks_w1.py, charter step 2) is a family like f2: in every text model's grid, read back from its order
    folders as w1 (never as f1), ordered through hard_bench_fleet_orders.py --pool w1, graded by its own payload."""

    def test_every_text_model_gets_w1_cells(self):
        cells = M.grid("mothership", {"qwen3.8:27b": {"completion", "thinking"}, "qwen3-vl:8b": {"completion", "vision"}})
        self.assertEqual(count(cells, model="qwen3.8:27b", fam="w1"), count(cells, model="qwen3.8:27b", fam="f2"))
        self.assertGreater(count(cells, model="qwen3-vl:8b", fam="w1"), 0)

    def test_w1_has_its_own_grader_and_rigby_payload(self):
        self.assertEqual(M.GRADE_PAYLOAD["w1"], "map_swarm_code_hard_w1.py")
        self.assertIn("w1", M.RIGBY_FAMS)
        self.assertTrue(M.RIGBY_PAYLOAD["w1"].endswith("/map_swarm_code_hard_w1.py"))
        self.assertEqual(M.rigby_fam("hardbench-rigby-m-x-w1-t06-off-s1.results.json"), "w1")
        self.assertEqual(len(M.rigby_tasks("w1")), 24)

    def test_a_w1_gpu_cell_is_submitted_with_its_pool(self):
        seen = {}

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""

        def run(cmd, **k):
            seen["cmd"] = cmd
            return R()
        with mock.patch.object(M.subprocess, "run", run):
            ok, tag, why = M.submit_cell("mothership", "qwen3.8:27b", "w1", "off", "0.6", "1")
        self.assertTrue(ok, why)
        self.assertTrue(seen["cmd"][1].endswith("hard_bench_fleet_orders.py"), seen["cmd"][:3])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--pool") + 1], "w1")
        self.assertIn("-w1-", tag, "the tag carries the family, so the history reader finds it again")


class VoidedDeliveryIsReordered(unittest.TestCase):
    """two gemma3:27b cells ran in a spilled runner at a third of their speed. A voided delivery is not a
    price: the cell re-orders (like a human cancel, never toward the retired-twice park) and prices exclude it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "fleet-g-mo-x", "order_mothership", "out.json")
        os.makedirs(os.path.dirname(self.out))
        put(self.out, {"rows": []})
        self.rec = {"cell": ("mothership", "gemma3:27b", "w1", "off", "1.0", "1"), "out": self.out,
                    "host": "mothership", "names": ["order_mothership"]}

    def tearDown(self):
        shutil.rmtree(self.tmp)
        M._VOIDS.clear()

    def _with(self, voids):
        M._VOIDS.clear()
        M._VOIDS.append({M.MV._key(k): v for k, v in voids.items()})

    def test_a_voided_delivery_is_not_delivered_and_its_cell_reorders(self):
        self._with({self.out: {"why": "spilled runner"}})
        self.assertIsNone(M.delivered_out(self.rec))
        seen = M.ledger({"order_mothership": {"status": "done"}}, [self.rec])
        e = seen[self.rec["cell"]]
        self.assertEqual(M.cell_state(e), "dead")
        go, attempt, why = M.to_order(e)
        self.assertTrue(go, why)
        self.assertEqual(e["retired"], 0, "a void is a human decision, never a step toward the machinery park")

    def test_without_a_void_the_same_delivery_counts(self):
        self._with({})
        self.assertEqual(M.delivered_out(self.rec), self.out)
        self.assertEqual(M.cell_state(M.ledger({}, [self.rec])[self.rec["cell"]]), "delivered")

    def test_a_resumed_folder_of_a_voided_order_is_voided_too(self):
        resumed = os.path.dirname(self.out) + ".20261004-001500"
        self.assertTrue(M.MV.is_voided(os.path.join(resumed, "out.json"), {M.MV._key(self.out): {}}))
        self.assertFalse(M.MV.is_voided(os.path.join(self.tmp, "other", "out.json"), {M.MV._key(self.out): {}}))

    def test_an_unreadable_void_list_raises_rather_than_reading_as_empty(self):
        bad = os.path.join(self.tmp, "menu_void.json")
        with open(bad, "w") as f:
            f.write("{not json")
        with self.assertRaises(ValueError):
            M.MV.load(bad)


class RigbyDeployStopIsStillOutstanding(unittest.TestCase):
    """A deploy stops a running rigby order and requeues it from its checkpoint (fleet_deploy_tolkien); the stop ships a
    partial results file first. Counting that as delivered re-orders pairs the requeued order is about to run."""
    NAME = "hardbench-rigby-m-qwen2.5-14b-instruct-q4-k-m-w1-t06-off-s1.20261003-1508"
    REAL_CAUSE = ("fleet_deploy 20261003-195140: stop for a deploy (answered rows are checkpointed; "
                  "requeued after the recreate)")                    # copied from the 19:51:59 results file

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.subs = os.path.join(self.tmp, "subs.json")
        put(self.subs, {self.NAME: {"cell": ["rigby", "m", "w1", "off", "0.6", "1"], "items": [], "at": "x"}})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def result(self, stamp, **kw):
        put(os.path.join(self.tmp, "%s.%s.results.json" % (self.NAME, stamp)), dict({"rows": []}, **kw))

    def outstanding(self):
        with mock.patch.object(M, "RIGBY_SUBS", self.subs):            # never the live stop record
            return M.rigby_outstanding(outbox=self.tmp, stopped_by_filler=set())[1]

    def test_the_deploy_tools_own_matcher_knows_the_real_cause(self):
        match = M._deploy_stop_matcher()
        self.assertTrue(match(self.REAL_CAUSE), "positive control: the bytes a real stop wrote")
        self.assertFalse(match("the owner: stop the work and deploy the Tolkien fix"))
        self.assertFalse(match(None))

    def test_a_deploy_stop_leaves_the_order_outstanding(self):
        self.result("20261003-195159", stopped=True, stop_cause=self.REAL_CAUSE)
        self.assertIn(self.NAME, self.outstanding())

    def test_the_requeued_orders_own_delivery_ends_it(self):
        self.result("20261003-195159", stopped=True, stop_cause=self.REAL_CAUSE)
        self.result("20261003-221500", stopped=False, complete=True)
        self.assertNotIn(self.NAME, self.outstanding())

    def test_any_other_stop_is_delivered_as_before(self):
        self.result("20261003-195159", stopped=True, stop_cause="AUTO stop rule (priced out) [rigby/x/f1/on]")
        self.assertNotIn(self.NAME, self.outstanding())

    def test_a_deploy_stopped_order_the_filler_later_stopped_has_ended(self):
        """four such orders read as outstanding forever, filled rigby's queue budget, rigby idled 3 h."""
        self.result("20261003-195159", stopped=True, stop_cause=self.REAL_CAUSE)
        with mock.patch.object(M, "RIGBY_SUBS", self.subs):
            out = M.rigby_outstanding(outbox=self.tmp, stopped_by_filler={self.NAME})[1]
        self.assertNotIn(self.NAME, out)

    def test_an_unreadable_newest_file_is_not_called_delivered(self):
        with open(os.path.join(self.tmp, "%s.20261003-195159.results.json" % self.NAME), "w") as f:
            f.write("{not json")
        self.assertIn(self.NAME, self.outstanding())


class VendorTemperatureCell(unittest.TestCase):
    """the owner every arm is also priced at its vendor's own temperature, previous models included."""
    CARRIED = {"qwen3.8:27b": "1", "qwen3:8b": "0.6", "gemma3:27b": "1", "nemotron-3-nano:30b-iq4xs": "1",
               "medgemma:27b": None}
    MODELS = {"qwen3.8:27b": ["completion", "thinking"], "qwen3:8b": ["completion", "thinking"],
              "gemma3:27b": ["completion"], "nemotron-3-nano:30b-iq4xs": ["completion", "thinking"],
              "medgemma:27b": ["completion"]}

    def test_the_tag_value_for_thinking_and_the_cards_value_without(self):
        self.assertEqual(M.vendor_temp("qwen3.8:27b", "on", "f1", "1"), "1.0")
        self.assertEqual(M.vendor_temp("qwen3.8:27b", "off", "f1", "1"), "0.7")
        self.assertEqual(M.vendor_temp("qwen3:8b", "off", "f1", "0.6"), "0.7")
        self.assertEqual(M.vendor_temp("qwen3-coder:30b", "off", "f1", "0.7"), "0.7")
        self.assertEqual(M.vendor_temp("nemotron-3-nano:30b-iq4xs", "on", "tool", "1"), "0.6")
        self.assertEqual(M.vendor_temp("nemotron-3-nano:30b-iq4xs", "on", "w1", "1"), "1.0")
        self.assertIsNone(M.vendor_temp("medgemma:27b", "off", "f1", None), "no stated temperature, no cell")

    def test_tag_temperature_reads_the_show_reply(self):
        show = {"parameters": 'top_k                          20\ntemperature                    1\nstop "<|im_end|>"'}
        self.assertEqual(M.tag_temperature(show), "1")
        self.assertIsNone(M.tag_temperature({"parameters": "top_k 20"}))
        self.assertIsNone(M.tag_temperature({}))

    def test_one_vendor_cell_per_arm_at_seed_set_1_and_none_where_it_is_a_core_knob(self):
        cells = M.grid("mothership", self.MODELS, self.CARRIED)
        kept = M.core(cells, self.CARRIED)
        extra = [c for c in kept if (c[4], c[5]) not in M.CORE_KNOBS]
        arms = {c[1:4] for c in cells}
        by_arm = collections.Counter(c[1:4] for c in extra)
        self.assertEqual(set(by_arm.values()), {1}, "at most one vendor cell per arm")
        self.assertEqual({c[5] for c in extra}, {"1"})
        self.assertNotIn(("qwen3:8b", "f1", "on"), by_arm, "0.6 is already a core knob")
        self.assertIn(("qwen3:8b", "f1", "off"), by_arm, "its card's non-thinking 0.7 is not")
        self.assertNotIn(("nemotron-3-nano:30b-iq4xs", "tool", "on"), by_arm, "tool calling is 0.6 = core")
        self.assertFalse({a for a in arms if a[0] == "medgemma:27b"} & set(by_arm))
        self.assertIn(("qwen3.8:27b", "f1", "on", "1.0", "1"), [c[1:] for c in extra])
        self.assertEqual(M.core(cells), [c for c in kept if (c[4], c[5]) in M.CORE_KNOBS],
                         "without the tag temperatures the filler orders exactly the core cells")

    def test_an_automatic_stop_leaves_the_vendor_cell_open_and_still_holds_the_rest(self):
        auto = {"mothership/qwen3.8:27b/f1/off": {"why": "AUTO stop rule (cannot fit)"},
                "mothership/qwen3:8b/f1/on": {"why": "AUTO stop rule (priced out)"}}
        opened = M.open_vendor_cells(auto, lambda box: self.CARRIED)
        self.assertEqual(opened["mothership/qwen3.8:27b/f1/off"]["except"], ["0.7/1"])
        self.assertNotIn("except", opened["mothership/qwen3:8b/f1/on"], "its vendor value is a core knob")
        self.assertIsNone(M.cell_hold(opened, ("mothership", "qwen3.8:27b", "f1", "off", "0.7", "1")))
        self.assertIsNotNone(M.cell_hold(opened, ("mothership", "qwen3.8:27b", "f1", "off", "0.6", "1")))
        self.assertIsNotNone(M.cell_hold(opened, ("mothership", "qwen3.8:27b", "f1", "off", "0.7", "4")))
        self.assertNotIn("except", auto["mothership/qwen3.8:27b/f1/off"], "the stop rule's own record is not edited")

    def test_a_human_hold_still_holds_the_vendor_cell(self):
        human = {"mothership/gemma3:27b": "paused"}
        self.assertIsNotNone(M.cell_hold(human, ("mothership", "gemma3:27b", "f1", "off", "1.0", "1")))

    def test_rigby_prices_qwen25_at_07_and_the_moe_at_1_thinking_07_without(self):
        models = ["Qwen2.5-7B-Instruct-Q4_K_M.gguf", "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf"]
        kept = M.core(M.rigby_grid(models), M.rigby_carried(models))
        extra = {c[1:5] for c in kept if (c[4], c[5]) not in M.CORE_KNOBS}
        self.assertIn(("Qwen2.5-7B-Instruct-Q4_K_M.gguf", "f1", "off", "0.7"), extra)
        self.assertIn(("Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "f1", "on", "1.0"), extra)
        self.assertIn(("Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "f1", "off", "0.7"), extra)
        self.assertEqual(len(extra), len(M.RIGBY_FAMS) * 3, "every rigby arm (7B x5, MoE x5 x2) gets one")

    def test_the_ledger_reads_a_vendor_order_back_at_its_own_temperature(self):
        seen = {}

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""

        def run(cmd, **k):
            seen["cmd"] = cmd
            return R()
        with mock.patch.object(M.subprocess, "run", run):
            ok, tag, _why = M.submit_cell("mothership", "qwen3.8:27b", "f1", "on", "1.0", "1")
        self.assertTrue(ok)
        self.assertIn("-t10-", tag)
        t = seen["cmd"][seen["cmd"].index("--temperature") + 1]
        self.assertEqual("%.1f" % float(t), "1.0", "records() keys the cell by '%.1f' of the order's --temperature")


class EffortLevelArm(unittest.TestCase):
    """qwen3.8 renders think=true as MEDIUM; its vendor default xhigh is its own arm, sent as the level string."""

    def test_grid_adds_xhigh_only_for_models_whose_true_renders_below_default(self):
        cells = M.grid("mothership", {"qwen3.8:27b": ["completion", "thinking"], "qwen3.5:9b": ["completion", "thinking"],
                                      "plain": ["completion"]})
        thinks = collections.defaultdict(set)
        for c in cells:
            thinks[c[1]].add(c[3])
        self.assertEqual(thinks["qwen3.8:27b"], {"off", "on", "xhigh"})
        self.assertEqual(thinks["qwen3.5:9b"], {"off", "on"})
        self.assertEqual(thinks["plain"], {"off"})

    def test_think_label_is_one_derivation(self):
        self.assertEqual(M.think_label(["--model", "m", "--think-level", "xhigh"]), "xhigh")
        self.assertEqual(M.think_label(["--think"]), "on")
        self.assertEqual(M.think_label([]), "off")

    def test_an_xhigh_cell_is_submitted_with_the_level(self):
        seen = []

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""
        with mock.patch.object(M.subprocess, "run", lambda cmd, **k: (seen.append(cmd), R())[1]):
            M.submit_cell("mothership", "qwen3.8:27b", "f2", "xhigh", "0.6", "1")
            M.submit_cell("mothership", "qwen3.8:27b", "tool", "xhigh", "0.6", "1")
            M.submit_cell("mothership", "qwen3.8:27b", "f2", "on", "0.6", "1")
        for cmd in seen[:2]:
            i = cmd.index("--think-level")
            self.assertEqual(cmd[i + 1], "xhigh")
            self.assertNotIn("--think", cmd)
        self.assertIn("--think", seen[2])
        self.assertNotIn("--think-level", seen[2])
        self.assertIn("-xhigh-", seen[0][seen[0].index("--tag") + 1], "the arm's own product folder")

    def test_an_xhigh_hold_key_parses(self):
        self.assertEqual(M.parse_arm("mothership/qwen3.8:27b/f2/xhigh"), ("mothership", "qwen3.8:27b", "f2", "xhigh"))


@unittest.skipUnless(M.STRATA, "STRATA_PLANS_DIR unset: the lab's own Strata arm is off on this tree")
class StrataServedModel(unittest.TestCase):
    """Flash-Next (#34 Phase 2 preliminary winner) is priced on STRATA - a model ollama does not list. It
    is named in STRATA, joins the box's listing, gets only its off + xhigh arms, and its orders carry the serving plan
    and the A1 test's context, pinned to the box."""
    FN = ("mothership", "qwen3.8-flash-next-strata-q2_0")

    def test_the_box_listing_names_it_with_its_vendor_temperature(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        carried = {}
        boxes = {"mothership": "http://mothership.example:11434", "bighonker": "http://bighonker.example:11434"}
        with mock.patch.object(M.urllib.request, "urlopen", lambda *a, **k: Resp(b'{"models": []}')), \
                mock.patch.dict(M.BOX, boxes):      # the boxes' own addresses are lab settings; the listing is the test
            models = M.box_models("mothership", carried)
            other = M.box_models("bighonker", {})
        self.assertEqual(models[self.FN[1]], ["completion", "thinking", "tools"])
        self.assertEqual(carried[self.FN[1]], "0.6")
        self.assertNotIn(self.FN[1], other, "only the box that serves it")

    def test_its_grid_is_one_top_config_cell_per_family(self):
        """A role test, not a grid: xhigh at the vendor temperature, seed set 1, once per family."""
        cells = M.grid("mothership", {self.FN[1]: list(M.STRATA[self.FN]["caps"])}, {self.FN[1]: "0.6"})
        self.assertEqual(sorted(cells), sorted(("mothership", self.FN[1], f, "xhigh", "0.6", "1")
                                               for f in ("f1", "f2", "w1", "c1", "tool")))
        self.assertEqual(len(M.core(cells, {self.FN[1]: "0.6"})), 5, "the core filter keeps all five")

    def test_its_orders_carry_the_serving_plan_and_the_tested_context(self):
        seen = []

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""
        with mock.patch.object(M.subprocess, "run", lambda cmd, **k: (seen.append(cmd), R())[1]):
            M.submit_cell(*self.FN, "w1", "xhigh", "0.6", "1")
            M.submit_cell(*self.FN, "tool", "off", "0.0", "1")
        plan = M.STRATA[self.FN]["plan"]
        for cmd in seen:
            self.assertEqual(cmd[cmd.index("--strata") + 1], plan)
            self.assertNotIn("--offload-ok", cmd)
        self.assertEqual(seen[0][seen[0].index("--num-ctx") + 1], "262144", "its trained context, measured 10-18")
        self.assertEqual(seen[0][seen[0].index("--only-host") + 1], "mothership")
        self.assertEqual(seen[1][seen[1].index("--ctx") + 1], "262144")
        self.assertEqual(seen[1][seen[1].index("--host") + 1], "mothership")
        if not os.path.isdir(os.path.dirname(plan)):     # a tree with no serving plans at all (a published one)
            self.skipTest("no serving plans here: the lab's measured plans stay with the lab")
        self.assertTrue(os.path.exists(plan), "the serving plan the orders name must exist")

    def test_the_preliminary_winner_is_priced_on_the_champions_terms(self):
        """The champion's role cells are top-limit tests - no at-cap brake on W1, and the filler passes no price line
        (a price line on them would measure the line, not the model)."""
        self.assertTrue(M.is_champion(*self.FN))
        self.assertTrue(M.is_champion("mothership", "qwen3.8:27b"))
        self.assertFalse(M.is_champion("bighonker", self.FN[1]))
        seen = []

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""
        with mock.patch.object(M.subprocess, "run", lambda cmd, **k: (seen.append(cmd), R())[1]):
            M.submit_cell(*self.FN, "w1", "xhigh", "0.6", "1")
        self.assertNotIn("--at-cap-brake", seen[0])
        self.assertNotIn("--price-ceiling-s", seen[0])


class UnreadAcknowledged(unittest.TestCase):
    """A known gap is stated once with its reason; only a NEW one is flagged (an always-red line gets skipped)."""

    def test_prefix_acknowledged_the_rest_flagged(self):
        ack = {"hardbench-fleet-g-bi-joy": "held 10-02", "hardbench-fleet-g-bi-joy-special": "longer prefix wins"}
        names = ["hardbench-fleet-g-bi-joy-f1", "hardbench-fleet-g-bi-joy-special-x", "hardbench-fleet-g-bi-new-w1",
                 "hardbench-fleet-g-bi-joy-f1"]
        open_, known = MP.split_acknowledged(names, ack)
        self.assertEqual(open_, ["hardbench-fleet-g-bi-new-w1"])
        self.assertEqual(known["hardbench-fleet-g-bi-joy-f1"], "held 10-02")
        self.assertEqual(known["hardbench-fleet-g-bi-joy-special-x"], "longer prefix wins")
        self.assertEqual(len(known), 2, "a name listed twice is one gap")

    def test_no_acknowledgements_flags_everything(self):
        self.assertEqual(MP.split_acknowledged(["a", "b"], {}), (["a", "b"], {}))


class DeliveredGeneration(unittest.TestCase):
    """a requeued order re-delivers into `<order dir>.<stamp>\\out.json` while submission.json names the bare
    dir; reading only that path dropped BigHonker's A3B W1 thinking-on (75 rows) and its requeued cells from the menu."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.bare = os.path.join(self.d, "hardbench-x_bighonker")
        self.out = os.path.join(self.bare, "out.json")
        os.makedirs(self.bare)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _gen(self, stamp, write=True, age=0):
        g = os.path.join(self.d, "hardbench-x_bighonker." + stamp)
        os.makedirs(g)
        if write:
            p = os.path.join(g, "out.json")
            open(p, "w").close()
            os.utime(p, (1_000_000 + age, 1_000_000 + age))
            return p
        return None

    def test_the_bare_path_when_it_is_the_only_delivery(self):
        open(self.out, "w").close()
        self.assertEqual(MP.delivered_out(self.out), self.out)

    def test_a_stamped_redelivery_is_found_when_the_bare_dir_holds_none(self):
        self._gen("20261018-123941", write=False)              # stopped mid-run: a partial, no out.json
        newest = self._gen("20261018-154525")
        self.assertEqual(MP.delivered_out(self.out), newest)

    def test_the_newest_generation_wins(self):
        open(self.out, "w").close()
        os.utime(self.out, (1_000_000, 1_000_000))
        older = self._gen("20261018-120000", age=10)
        newer = self._gen("20261018-150000", age=20)
        self.assertEqual(MP.delivered_out(self.out), newer)
        self.assertNotEqual(MP.delivered_out(self.out), older)

    def test_a_sibling_that_only_shares_a_prefix_is_not_a_generation(self):
        other = os.path.join(self.d, "hardbench-x_bighonker.extra")
        os.makedirs(other)
        open(os.path.join(other, "out.json"), "w").close()
        self.assertIsNone(MP.delivered_out(self.out))

    def test_nothing_delivered(self):
        self.assertIsNone(MP.delivered_out(self.out))
        self.assertIsNone(MP.delivered_out(None))


class PlacementEpoch(unittest.TestCase):
    """rigby's 1060 moved from an x1 to a full-width riser (reads 3.5x, answers token-identical). A rigby
    menu row is keyed by its model FILE, so x1 and x16 deliveries of the same file would pool into one price. The
    placement's `since` makes the filler re-order the file's older cells and build_menu supersede their rows once the
    setting has newer ones - until then they still price."""
    MTP = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
    CPU = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    OLD = r"E:\x\hardbench-rigby-m-a-c1-t06-off-s1.20261016-0823.20261016-085950.results.json"
    NEW = r"E:\x\hardbench-rigby-m-a-c1-t06-off-s1.20261027-0100.20261027-011500.results.json"

    def test_the_stamp_is_read_from_the_file_name(self):
        import datetime
        self.assertEqual(M.delivered_at(self.OLD), datetime.datetime(2026, 10, 16, 8, 59, 50))
        self.assertIsNone(M.delivered_at("hardbench-rigby-m-a-c1.results.json"))

    def test_only_a_placed_file_before_its_epoch_is_stale(self):
        self.assertTrue(M.placement_stale(self.MTP, self.OLD))
        self.assertFalse(M.placement_stale(self.MTP, self.NEW))
        self.assertTrue(M.placement_stale(self.MTP, "no-stamp.results.json"), "an unknown era re-measures")
        self.assertFalse(M.placement_stale(self.CPU, self.OLD), "a CPU file has no placement epoch")

    def _row(self, path, model=None, temp="0.6", usable="True"):
        return {"box": "rigby", "family": "chat-c1", "shape": "S5r", "model": model or self.MTP, "think": "off",
                "temperature": temp, "source_file": path, "usable": usable, "void": ""}

    def test_old_rows_are_superseded_only_where_the_setting_was_re_measured(self):
        old_re, new, old_alone = self._row(self.OLD), self._row(self.NEW), self._row(self.OLD, temp="0.7")
        cpu, fleet = self._row(self.OLD, model=self.CPU), dict(self._row(self.OLD), box="bighonker")
        n = M.supersede_pre_placement([old_re, new, old_alone, cpu, fleet])
        self.assertEqual(n, 1)
        self.assertEqual((old_re["usable"], new["usable"]), ("False", "True"))
        self.assertIn("gtx1060-standard", old_re["void"])
        self.assertEqual([r["usable"] for r in (old_alone, cpu, fleet)], ["True"] * 3,
                         "a setting with no newer rows keeps pricing; other files and boxes are untouched")

    def test_the_filler_re_orders_a_stale_cell(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, "outbox"))
        rows = [{"task": "ctx_approver", "replicate": r, "model": self.MTP, "arm": "rigby", "temperature": 0.6,
                 "reasoning": "off", "spec_type": "draft-mtp", "ran": True, "ok": True, "wall_s": 9.0,
                 "prompt_tokens": 2052} for r in (1, 2, 3)]
        res = {"rows": rows, "control_passed": True, "cohort": {"arms": [{"arm": "rigby", "ctx": 32768}]}}
        for p in (self.OLD, self.NEW):
            put(os.path.join(d, "outbox", os.path.basename(p)), res)
        with mock.patch.object(M, "TABJOBS", d):
            cells, *_ = M.rigby_history()
        self.assertEqual(sum(len(v["pairs"]) for v in cells.values()), 3, "only the epoch's delivery counts")
        os.remove(os.path.join(d, "outbox", os.path.basename(self.NEW)))
        with mock.patch.object(M, "TABJOBS", d):
            cells, *_ = M.rigby_history()
        self.assertEqual(cells, {}, "a cell measured only before the epoch is unmeasured: the filler re-orders it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
