"""menu_policy: the priced menu becomes Spock's ordered policy table, keyed by the design's triage tiers, with
acceptability taken from the filler's and the owner's stop verdicts (no floor of its own)."""
import unittest
from unittest import mock

import advisor_tasks
import menu_policy as P


def row(box, model, success, n, s, j, family="coding-f2", shape="S1", status="holds", think="off", t="0.6"):
    return {"box": box, "family": family, "shape": shape, "model": model, "think": think, "temperature": t,
            "n": str(n), "success": str(success), "s_per_solve": "" if s is None else str(s),
            "j_per_solve": "" if j is None else str(j), "status": status}


def boxes(lst):
    return [(e["box"], e["role"]) for e in lst]


STOPS = {
    "softserve/slow/f2/off": {"why": "AUTO stop rule (priced out): ...", "except": ["0.6/1"]},
    "rigby/nothink": "the owner's ruling: whole model",
    "rigby/tiny/f2/off": {"why": "AUTO stop rule (priced out): rigby"},
}


class Policy(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("mothership", "qwen3.8:27b", 1.0, 100, 0.5, 120.0),
            row("bighonker", "mid", 0.97, 100, 1.0, 60.0),
            row("softserve", "mid", 0.96, 100, 4.0, 50.0),
            row("softserve", "slow", 0.99, 100, 9.0, 40.0),                 # stopped by the filler
            row("fellowship", "tiny", 0.40, 100, 2.0, None),                # dominated, NOT stopped -> stays
            row("fellowship", "tiny", 1.0, 100, 1.0, None, shape="S2:best_of_n"),
            row("rigby", "moe", 0.99, 100, 1.2, None),
            row("rigby", "tiny", 0.30, 100, 3.0, None, think="default"),    # filler keys "default" as "off"
            row("rigby", "nothink", 0.90, 100, 1.0, None, think="n/a"),     # whole-model ruling
            row("bighonker", "huge", 0.0, 60, None, None, status="VOID: cannot fit (40 of 60 at the cap)"),
        ]
        self.fam = P.compile_policy(self.rows, STOPS)["coding-f2"]

    def test_tiers_are_the_advisors_labels_plus_the_fellowship_rungs(self):
        labels = set(advisor_tasks.TIERS) - {"tool"}
        self.assertLessEqual(labels, set(self.fam["tiers"]), "every triage label must have a table tier to place in")
        self.assertEqual(set(self.fam["tiers"]) - labels, {"rigby", "tablets"})

    def test_refusals_come_from_the_stop_verdicts_not_a_floor(self):
        why = {(e["box"], e["model"], e["shape"]): e["why"] for e in self.fam["refused"]}
        self.assertTrue(why[("softserve", "slow", "S1")].startswith("stopped: AUTO"))
        self.assertTrue(why[("rigby", "tiny", "S1")].startswith("stopped: AUTO"))
        self.assertTrue(why[("rigby", "nothink", "S1")].startswith("stopped: the owner's ruling"))
        self.assertTrue(why[("fellowship", "tiny", "S2:best_of_n")].startswith("oracle"))
        self.assertIn("VOID", why[("bighonker", "huge", "S1")])
        self.assertEqual(len(why), 5)
        # dominated is not a reason to stop: the 40% tablet arm stays placed, priced by its per-solve cost
        self.assertIn(("fellowship", "home"), boxes(self.fam["tiers"]["fellowship"]["fastest"]))

    def test_except_cells_do_not_reopen_a_stopped_arm_for_placement(self):
        p = P.compile_policy([row("softserve", "slow", 0.99, 100, 9.0, 40.0, t="0.6")], STOPS)["coding-f2"]
        self.assertEqual(len(p["refused"]), 1)

    def test_an_orders_only_hold_stops_orders_but_the_rows_still_place(self):
        """cutting BigHonker to role cells (a scope cut) had refused every BigHonker A3B row from placement."""
        stops = {"bighonker/cut/f2/off": {"why": "scope cut", "orders_only": True},
                 "bighonker/judged/f2/off": {"why": "a verdict on the arm"}}
        p = P.compile_policy([row("bighonker", "cut", 1.0, 72, 2.0, 200.0), row("bighonker", "judged", 1.0, 72, 2.0, 200.0)],
                             stops)["coding-f2"]
        self.assertEqual([e["model"] for e in p["refused"]], ["judged"])
        self.assertIn("cut", [e["model"] for e in p["tiers"]["8gb"]["best_quality"]])

    def test_lead_tier_home_first_then_8gb_overflow(self):
        self.assertEqual(boxes(self.fam["tiers"]["lead"]["fastest"]),
                         [("mothership", "home"), ("bighonker", "overflow"), ("softserve", "overflow")])
        bh = self.fam["tiers"]["lead"]["fastest"][1]
        self.assertIn("overflow from the lead tier", bh["caveats"])

    def test_least_energy_lens(self):
        # 8gb home: softserve 50 J < BH 60 J; overflow: rigby 1.2 s x its whole box (44 idle + 54) = 117.6 J < MS 120 J
        self.assertEqual(boxes(self.fam["tiers"]["8gb"]["least_energy"]),
                         [("softserve", "home"), ("bighonker", "home"), ("rigby", "overflow"),
                          ("mothership", "overflow"), ("fellowship", "overflow")])
        rig = self.fam["tiers"]["8gb"]["least_energy"][2]
        self.assertAlmostEqual(rig["j_per_solve"], round(1.2 * P.rigby_watts(rig["model"])[0], 1))

    def test_one_entry_per_model_per_box_per_lens_and_the_boxs_winner_first(self):
        """the owner every model's winning config on each box has a place; a model that loses a lens on its box
        stays in the table, because the weighing alone knows when it is already loaded there."""
        p = P.compile_policy(self.rows + [row("bighonker", "mid2", 0.98, 100, 0.8, 90.0),
                                          row("bighonker", "mid2", 0.97, 100, 0.7, 95.0, t="0.0")], STOPS)["coding-f2"]
        fastest = p["tiers"]["8gb"]["fastest"]
        on_bh = [e["model"] for e in fastest if e["box"] == "bighonker"]
        self.assertEqual(on_bh, ["mid2", "mid"], "the lens winner first, then the model it beat; one row per model")
        self.assertEqual(next(e for e in fastest if e["model"] == "mid2")["temperature"], "0.0", "that model's own winner")


class ThinkingOffOnlyOnATie(unittest.TestCase):
    """the owner thinking off is never a default except where the menu shows a quality tie. The fastest lens
    chose off on every chat cell because off is cheaper per solve."""

    def fam(self, rows):
        return P.compile_policy(rows, {})["chat-c1"]

    def test_off_that_clearly_loses_is_refused_and_on_takes_the_fastest_row(self):
        f = self.fam([row("bighonker", "qwen3.5:9b", 0.845, 120, 2.0, 80.0, family="chat-c1", think="off"),
                      row("bighonker", "qwen3.5:9b", 0.927, 120, 3.1, 120.0, family="chat-c1", think="on")])
        fastest = f["tiers"]["8gb"]["fastest"]
        self.assertEqual([(e["box"], e["think"]) for e in fastest], [("bighonker", "on")])
        self.assertTrue(any(e["think"] == "off" and "only on a tie" in e["why"] for e in f["refused"]))

    def test_a_tie_keeps_off_so_the_fastest_lens_may_still_choose_it(self):
        f = self.fam([row("bighonker", "qwen3:8b", 0.98, 40, 1.7, 60.0, family="chat-c1", think="off"),
                      row("bighonker", "qwen3:8b", 1.0, 40, 3.0, 90.0, family="chat-c1", think="on")])
        self.assertEqual(f["tiers"]["8gb"]["fastest"][0]["think"], "off", "on's lower bound is below off: a tie")
        self.assertEqual(f["refused"], [])

    def test_a_model_without_a_thinking_switch_is_never_refused_for_it(self):
        f = self.fam([row("rigby", "Qwen2.5-1.5B", 0.9, 40, 1.9, None, family="chat-c1", think="default")])
        self.assertEqual(f["refused"], [])


class FellowshipRungs(unittest.TestCase):
    """`fellowship` is Tolkien's whole destination (rigby + the tablets); `rigby` and `tablets` are the two rungs inside
    it, ADDED beside it so a request can name the class of worker it needs and nothing that placed before moves."""
    BEFORE_HOMES = {"fellowship": ("rigby", "fellowship"), "8gb": ("bighonker", "softserve"), "lead": ("mothership",)}
    BEFORE_OVERFLOW = {"fellowship": ("8gb", "lead"), "8gb": ("lead", "fellowship"), "lead": ("8gb",)}

    def setUp(self):
        self.rows = [row("mothership", "qwen3.8:27b", 1.0, 100, 0.5, 120.0),
                     row("bighonker", "mid", 0.97, 100, 1.0, 60.0),
                     row("softserve", "mid", 0.96, 100, 4.0, 50.0),
                     row("fellowship", "tiny", 0.40, 100, 2.0, None),
                     row("rigby", "moe", 0.99, 100, 1.2, None),
                     row("rigby", "tiny", 0.30, 100, 3.0, None, think="default")]    # stopped (STOPS), so it only refuses
        self.p = P.compile_policy(self.rows, STOPS)["coding-f2"]

    def test_each_rung_homes_only_its_own_boxes(self):
        t = self.p["tiers"]
        for lens in P.LENSES:
            self.assertEqual({e["box"] for e in t["rigby"][lens] if e["role"] == "home"}, {"rigby"}, lens)
            self.assertEqual({e["box"] for e in t["tablets"][lens] if e["role"] == "home"}, {"fellowship"}, lens)

    def test_the_union_tier_homes_exactly_what_the_two_rungs_home(self):
        t = self.p["tiers"]
        for lens in P.LENSES:
            union = {e["box"]: e for e in t["fellowship"][lens] if e["role"] == "home"}
            parts = {e["box"]: e for r in ("rigby", "tablets") for e in t[r][lens] if e["role"] == "home"}
            self.assertEqual(set(union), {"rigby", "fellowship"}, lens)
            self.assertEqual(union, parts, "one priced row per box, however the tier is named: " + lens)

    def test_adding_the_rungs_moves_nothing_that_placed_before(self):
        with mock.patch.object(P, "TIER_HOMES", self.BEFORE_HOMES), \
                mock.patch.object(P, "TIER_OVERFLOW", self.BEFORE_OVERFLOW):
            before = P.compile_policy(self.rows, STOPS)
        after = P.compile_policy(self.rows, STOPS)
        self.assertTrue(before and set(before) == set(after), "zero families compared - the join is broken")
        for fam in before:
            self.assertEqual(set(after[fam]["tiers"]) - set(before[fam]["tiers"]), {"rigby", "tablets"})
            for tier in before[fam]["tiers"]:
                self.assertEqual(after[fam]["tiers"][tier], before[fam]["tiers"][tier], (fam, tier))
            self.assertEqual(after[fam]["refused"], before[fam]["refused"])

    def test_a_rung_lists_its_homes_first_then_labelled_overflow(self):
        want = {"rigby": {"bighonker", "softserve", "mothership", "fellowship"},
                "tablets": {"rigby", "bighonker", "softserve", "mothership"}}
        for rung, over_boxes in want.items():
            for lens in ("least_energy", "best_quality"):
                rows = self.p["tiers"][rung][lens]
                roles = [e["role"] for e in rows]
                self.assertEqual(roles, sorted(roles, key=lambda r: r != "home"), (rung, lens))
                self.assertEqual({e["box"] for e in rows if e["role"] == "overflow"}, over_boxes, (rung, lens))
                for e in rows:
                    self.assertEqual("overflow from the %s tier" % rung in e["caveats"], e["role"] == "overflow")

    def test_every_tier_is_declared_both_ways_and_the_rungs_partition_the_union(self):
        self.assertEqual(set(P.TIER_HOMES), set(P.TIER_OVERFLOW))
        for tier, over in P.TIER_OVERFLOW.items():
            self.assertNotIn(tier, over, tier)
            self.assertLessEqual(set(over), set(P.TIER_HOMES), tier)
        rigby, tablets = set(P.TIER_HOMES["rigby"]), set(P.TIER_HOMES["tablets"])
        self.assertFalse(rigby & tablets, "a box homes one rung")
        self.assertEqual(rigby | tablets, set(P.TIER_HOMES["fellowship"]))


class FellowshipOrders(unittest.TestCase):
    """Each Fellowship row carries the Tolkien `llm` fields it was priced with, so Tolkien picks the worker from the
    table and the row that places a piece also sets it (design § D). Built by menu_fill's own builders (one derivation
    with rigby_submit); GPU rows carry none (their policy tokens are filled by the desk)."""
    MTP = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"

    def setUp(self):
        self.p = P.compile_policy([
            row("rigby", self.MTP, 1.0, 100, 4.8, None, t="0.6"),
            row("fellowship", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", 0.42, 100, 31.0, None, think="n/a"),
            row("fellowship", "Qwen3.5-0.8B-Q4_K_M @class B (warm)", 0.9, 100, 25.0, None, family="chat-turn", t=""),
            row("bighonker", "mid", 0.97, 100, 1.0, 60.0)], {})

    def _row(self, fam, box):
        return next(e for e in self.p[fam]["tiers"]["fellowship"]["least_energy"] if e["box"] == box)

    def test_rigby_row_is_the_standard_1060_order(self):
        o = self._row("coding-f2", "rigby")["order"]
        want = P.menu_fill.rigby_order_fields(self.MTP, "off", "0.6")
        self.assertEqual(o, want)
        self.assertEqual(o["arms"], ["rigby"])
        self.assertEqual(o["ctx"], P.menu_fill.RIGBY_CTX)
        for k, v in P.menu_fill.RIGBY_PLACEMENT[self.MTP]["fields"].items():
            self.assertEqual(o[k], v, k)
        self.assertEqual(o["reasoning"], "off")

    def test_tablet_row_is_one_warm_node_at_the_tablets_tested_context(self):
        o = self._row("coding-f2", "fellowship")["order"]
        self.assertEqual(o, {"arms": ["warm"], "model": "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", "warm_nodes": 1,
                             "ctx": P.menu_fill.TABLET_CTX, "temperature": 0.6})

    def test_a_class_annotation_is_not_part_of_the_model_and_a_blank_temperature_is_left_to_the_payload(self):
        o = self._row("chat-turn", "fellowship")["order"]
        self.assertEqual(o["model"], "Qwen3.5-0.8B-Q4_K_M")
        self.assertNotIn("temperature", o)
        self.assertEqual(o["reasoning"], "off")

    def test_gpu_rows_carry_no_fellowship_order(self):
        rows = self.p["coding-f2"]["tiers"]["8gb"]["least_energy"]
        self.assertTrue(rows)
        self.assertFalse([e for e in rows if e["box"] not in ("rigby", "fellowship") and "order" in e])

    def test_rigby_submit_orders_a_cell_with_the_same_fields(self):
        """The filler's own order for a cell and the table row agree field for field (one derivation)."""
        import tempfile
        import menu_fill as M
        d = tempfile.mkdtemp()
        with mock.patch.object(M, "TABJOBS", d), mock.patch.object(M, "RIGBY_SUBS", d + "\\subs.json"), \
                mock.patch.object(M, "rigby_ceiling", return_value=None):
            import os
            os.makedirs(os.path.join(d, "inbox"))
            name, _c = M.rigby_submit(("rigby", self.MTP, "f2", "off", "0.6", "1"), [("t1", 1)], {}, {})
            import json
            with open(os.path.join(d, "inbox", name + ".tjob.json"), encoding="utf-8") as f:
                spec = json.load(f)
        for k, v in self._row("coding-f2", "rigby")["order"].items():
            self.assertEqual(spec[k], v, k)


class TimeCostQuality(unittest.TestCase):
    """the owner speed, cost and quality - and a 1000 s reply to a chat is priced out."""

    def setUp(self):
        self.rows = [row("softserve", "q8", 0.65, 75, 1.8, 300.0, family="chat-c1"),
                     row("mothership", "qwen3.8:27b", 0.95, 75, 4.7, 2400.0, family="chat-c1"),
                     row("fellowship", "tiny", 0.10, 100, 1308.0, None, family="chat-c1")]
        self.p = P.compile_policy(self.rows, {})["chat-c1"]

    def test_a_1300_s_chat_is_priced_out_of_the_fast_lens_only(self):
        self.assertAlmostEqual(self.p["interactive_line_s"], 18.0)
        fell = self.p["tiers"]["fellowship"]
        self.assertNotIn("fellowship", [e["box"] for e in fell["fastest"]])
        self.assertIn("fellowship", [e["box"] for e in fell["least_energy"]], "background keeps it")

    def test_best_quality_lens_orders_by_quality(self):
        self.assertEqual([e["box"] for e in self.p["tiers"]["8gb"]["best_quality"]],
                         ["softserve", "mothership", "fellowship"])
        self.assertEqual(self.p["tiers"]["8gb"]["best_quality"][1]["role"], "overflow")
        self.assertEqual([e["box"] for e in self.p["tiers"]["lead"]["best_quality"]], ["mothership", "softserve"])


class ChatTurnFamily(unittest.TestCase):
    """reduce_chat1.py's standings-shaped rows place the simple-chat turn on every tier."""

    def test_chat_turn_rows_place_by_tier(self):
        rows = [row("rigby", "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", 1.0, 35, 2.0, None, family="chat-turn",
                    think="default"),
                row("bighonker", "qwen3:8b", 1.0, 21, 1.6, 196.0, family="chat-turn"),
                row("fellowship", "tiny @class B", 0.9, 35, 30.0, None, family="chat-turn", think="n/a")]
        t = P.compile_policy(rows, {})["chat-turn"]["tiers"]
        # the 30 s tablet turn is past 10x the fleet's fastest (1.6 s -> 16 s): out of `fastest`, kept for background
        self.assertEqual(boxes(t["fellowship"]["fastest"])[0], ("rigby", "home"))
        self.assertNotIn(("fellowship", "home"), boxes(t["fellowship"]["fastest"]))
        self.assertIn(("fellowship", "home"), boxes(t["fellowship"]["least_energy"]))
        self.assertEqual(boxes(t["8gb"]["fastest"])[0], ("bighonker", "home"))


class MothershipIsNotReserved(unittest.TestCase):
    """the owner the card unloads every model when idle - another model there is a load cost, not a refusal."""

    def test_any_model_on_mothership_is_placed_with_a_residency_caveat(self):
        p = P.compile_policy([row("mothership", "qwen3.8:27b", 1.0, 50, 5.0, 900.0),
                              row("mothership", "nemotron", 0.95, 50, 3.0, 600.0)], {})["coding-f2"]
        self.assertEqual(p["refused"], [])
        quick = p["tiers"]["lead"]["fastest"][0]
        self.assertEqual(quick["model"], "nemotron")
        self.assertTrue(any(c.startswith("residency") for c in quick["caveats"]))
        self.assertEqual(p["tiers"]["lead"]["best_quality"][0]["model"], "qwen3.8:27b")


class ChatIsNotLeadOnly(unittest.TestCase):
    """General chat may go on the other boxes and models too."""

    def test_8gb_chat_is_placed_whatever_the_champion_scores(self):
        rows = [row("mothership", "qwen3.8:27b", 1.0, 75, 9.0, 2400.0, family="chat-c1"),
                row("bighonker", "q3-8b", 0.83, 75, 14.8, 1500.0, family="chat-c1"),
                row("softserve", "q35-9b", 0.85, 75, 5.5, 900.0, family="chat-c1")]
        t = P.compile_policy(rows, STOPS)["chat-c1"]["tiers"]["8gb"]
        self.assertEqual(boxes(t["fastest"])[:2], [("softserve", "home"), ("bighonker", "home")])
        self.assertEqual(P.compile_policy(rows, STOPS)["chat-c1"]["refused"], [])


class LiveJoin(unittest.TestCase):
    """Positive control on the REAL files: the stop verdicts must match real standings rows ()."""

    def test_real_stops_match_real_rows(self):
        import csv
        import os
        if not os.path.exists(P.STANDINGS):
            self.skipTest("standings not reachable")
        with open(P.STANDINGS, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        pol = P.compile_policy(rows, P.load_stops())
        stopped = [e for p in pol.values() for e in p["refused"] if e["why"].startswith("stopped")]
        self.assertGreater(len(stopped), 0)
        self.assertIn("rigby", {e["box"] for e in stopped})     # the default->off think join works


def arm(name, team=None, lead=None, solved=0, scored=0, wall=0.0, energy=0.0, energy_rows=0, n=0, pieces=(), pj=None):
    rows = {"ex%d" % i: {"pieces": [p]} for i, p in enumerate(pieces)} or {"ex": {"pieces": []}}
    return {"name": name, "layer": "gpu", "team": team, "lead": lead or {},
            "totals": {"scored_solved": solved, "scored_n": scored, "wall_s": wall, "energy_j": energy,
                       "energy_rows": energy_rows, "n": n, "capped_rows": 0, "pieces_energy_j": pj,
                       "pieces_done": len(pieces)},
            "rows": rows}


def piece(wall, submitted, done):
    return {"status": "done", "helper_wall_s": wall, "submitted": submitted, "done": done}


Q38 = {"model": "qwen3.8:27b", "think": "xhigh", "num_ctx": 122880}


class RoleRows(unittest.TestCase):
    """Phase 7 step 5: lead and helper prices folded in from the reducer's summary, one derivation."""

    def test_solo_arms_of_one_lead_pool_and_the_crown_is_marked(self):
        # the crowned lead is whatever P.CROWN names; a lead that is
        # not the crown pools the same way and is not marked
        crowned = {"model": P.CROWN["model"], "think": P.CROWN["think"], "num_ctx": 262144}
        s = {"arms": [arm("spot_mothership", lead=crowned, solved=8, scored=9, wall=900.0, energy=9000.0, energy_rows=9, n=9),
                      arm("hard_mothership", lead=crowned, solved=3, scored=7, wall=200.0, energy=2000.0, energy_rows=7, n=7),
                      arm("other_mothership", lead=dict(Q38, model="not-the-crown"), solved=1, scored=2, wall=50.0,
                          energy=500.0, energy_rows=2, n=2)]}
        r = P.compile_roles(s)
        self.assertEqual(len(r["leads"]), 2)
        e = [x for x in r["leads"] if x["model"] == P.CROWN["model"]][0]
        self.assertEqual((e["solved"], e["scored"]), (11, 16))
        self.assertEqual(e["s_per_solve"], 100.0)
        self.assertEqual(e["j_per_solve"], 1000)
        self.assertTrue(e["crowned"])
        self.assertFalse([x for x in r["leads"] if x["model"] == "not-the-crown"][0]["crowned"])

    def test_rigby_energy_follows_the_files_placement(self):
        """one flat 54 W priced every rigby row - right for the CPU path, ~1.6x low for the 1060 (A3B writing
        +78/+111/+90/+81 W, median ~85). The watts come from menu_fill.RIGBY_PLACEMENT, the object the orders use."""
        mtp = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
        es = P.entries([row("rigby", mtp, 1.0, 10, 10.0, None), row("rigby", "Qwen2.5-7B", 1.0, 10, 10.0, None)])
        by = {e["model"]: e for e in es}
        self.assertEqual(by[mtp]["j_per_solve"], round(10.0 * P.rigby_watts(mtp)[0], 1))
        self.assertIn("gtx1060-standard", " ".join(by[mtp]["caveats"]))
        self.assertEqual(by["Qwen2.5-7B"]["j_per_solve"], round(10.0 * P.rigby_watts("Qwen2.5-7B")[0], 1))

    def test_rigby_is_priced_as_its_whole_box(self):
        """The menu prices the TOTAL draw at the wall - rigby's idle for the placement's era (its own
        PSU + the 1060's 600 W supply) PLUS the placement's working extra, never the extra alone."""
        mtp = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
        place = P.menu_fill.RIGBY_PLACEMENT[mtp]
        era = P.menu_wall.rigby_era(P.datetime.datetime.fromisoformat(place["since"]).timestamp())
        self.assertEqual(P.rigby_watts(mtp)[0], era["idle_w"] + place["watts"])
        self.assertGreater(era["idle_w"], 70, "the x16 era idles ~72-77 W at the wall (plug 46.9 + the 600 W supply)")
        self.assertEqual(P.rigby_watts("no-placement")[0], P.menu_wall.RIGBY[0]["idle_w"] + P.RIGBY_MARGINAL_W)
        late = P.menu_wall.rigby_era(P.datetime.datetime(2026, 10, 28).timestamp())
        self.assertEqual(P.rigby_watts("no-placement", at=P.datetime.datetime(2026, 10, 28).timestamp())[0],
                         late["idle_w"] + P.RIGBY_MARGINAL_W, "an unplaced model takes the idle of the era it ran in")

    def test_unmetered_rows_state_no_joules(self):
        s = {"arms": [arm("orn_mothership", lead={"model": "ornith", "think": True}, solved=2, scored=6, wall=600.0,
                          energy=0.0, energy_rows=0, n=6)]}
        e = P.compile_roles(s)["leads"][0]
        self.assertIsNone(e["j_per_solve"])
        self.assertFalse(e["crowned"])
        self.assertTrue(any("unmetered" in c for c in e["caveats"]))

    def test_helper_paths_stay_apart_and_price_per_piece(self):
        team = {"helper": "bighonker", "helper_model": "qwen3:8b"}
        s = {"arms": [arm("a_mothership", team=team, lead=Q38, pieces=[piece(4.0, 0, 40), piece(6.0, 100, 150)], pj=900),
                      arm("b_mothership", team=dict(team, window=True), lead=Q38, pieces=[piece(3.0, 0, 16)], pj=300)]}
        h = {x["path"]: x for x in P.compile_roles(s)["helpers"]}
        self.assertEqual(set(h), {"order", "window"})
        self.assertEqual(h["order"]["helper_wall_s"], 5.0)
        self.assertEqual(h["order"]["round_trip_s"], 45.0)
        self.assertEqual(h["order"]["j_per_piece"], 450)
        self.assertIn("one piece only", h["window"]["caveats"])

    def test_escalation_and_errand_delegation_are_both_decided(self):
        d = P.compile_roles({"arms": []})["lead_defaults"]
        self.assertEqual(d["escalation"]["value"], "hard code starts at the lead")
        self.assertEqual(d["escalation"]["decided"], "2026-10-10")
        self.assertEqual((d["delegation"]["value"], d["delegation"]["decided"]), ("the lead works alone", "2026-10-11"))

    def test_the_w1_judge_verdicts_are_recorded_with_their_evidence(self):
        """B3: ops gets a selector (tev1 4B @ 0.95, the lead keeps what it will not vouch for), reason gets
        none; the judge selects and never routes."""
        d = P.compile_roles({"arms": []})["lead_defaults"]
        ops, reason = d["ops_selector"], d["reason_selector"]
        self.assertTrue(ops["preliminary"])
        self.assertIn("0.95", ops["value"])
        self.assertIn("WHOLE to the lead", ops["value"])
        self.assertIn("w1_judge.md", ops["why"])
        self.assertTrue(reason["value"].startswith("none"))
        self.assertEqual({ops["decided"], reason["decided"]}, {"2026-10-17"})


STAGE_HEADER = ("arm,exercise,piece,host,submit_to_created_s,created_to_claimed_s,claimed_to_spawn_s,spawn_to_done_s,"
                "helper_wall_s,round_trip_s\n")


class FixedRoundTrip(unittest.TestCase):
    """desk-POST and preflight bugs come out of each historical piece from per-piece stage evidence."""

    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = os.path.join(self.tmp.name, "helper_piece_stages.csv")
        team = {"helper": "bighonker", "helper_model": "qwen3:8b"}
        pcs = [dict(piece(4.0, 0, 40), k=1), dict(piece(5.0, 100, 150), k=2), dict(piece(6.0, 200, 260), k=3)]
        rows = {"ex%d" % i: {"pieces": [p]} for i, p in enumerate(pcs)}
        a = arm("t_mothership", team=team, lead=Q38, pieces=pcs, pj=900)
        a["rows"] = rows
        self.summary = {"arms": [a]}

    def write(self, lines):
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(STAGE_HEADER + "".join(lines))

    def helper(self, **kw):
        return P.compile_roles(self.summary, stages=self.csv)["helpers"][0]

    def test_each_piece_loses_only_its_own_bug_time_and_the_median_is_taken(self):
        # piece 1: raw 40, POST 10.38 over by 10.0, preflight 8.0 over by 6.0 -> 24.0
        # piece 2: raw 50, POST 0.38 (nothing over), preflight 9.0 over by 7.0 -> 43.0
        # piece 3: raw 60, POST 5.38 over by 5.0, preflight 1.0 (below the floor: nothing) -> 55.0
        self.write(["t_mothership,ex0,1,bighonker,10.38,1,8.0,20,4,40\n",
                    "t_mothership,ex1,2,bighonker,0.38,1,9.0,30,5,50\n",
                    "t_mothership,ex2,3,bighonker,5.38,1,1.0,50,6,60\n"])
        h = self.helper()
        self.assertEqual(h["round_trip_s"], 50.0)                 # the raw median is untouched
        self.assertEqual(h["round_trip_s_fixed"], 43.0)           # median of 24, 43, 55
        self.assertEqual(h["round_trip_fixed_pieces"], 3)
        self.assertIn("helper_piece_stages.csv", h["round_trip_basis"])

    def test_a_missing_stage_removes_nothing_and_a_piece_without_evidence_is_left_out(self):
        self.write(["t_mothership,ex0,1,bighonker,10.38,1,,,4,40\n",     # preflight stage missing: only the POST goes
                    "t_mothership,ex1,2,bighonker,0.38,1,9.0,30,5,50\n"])   # piece 3 has no row at all
        h = self.helper()
        self.assertEqual(h["round_trip_s_fixed"], 36.5)           # median of 30.0 and 43.0
        self.assertEqual(h["round_trip_fixed_pieces"], 2)
        text = " ".join(h["caveats"])
        self.assertIn("1 of 3 timed pieces have no stage evidence", text)
        self.assertIn("lack a stage", text)

    def test_missing_csv_gives_none_and_a_caveat_never_a_guess(self):
        import os
        h = self.helper()                                          # nothing written: the file does not exist
        self.assertFalse(os.path.exists(self.csv))
        self.assertIsNone(h["round_trip_s_fixed"])
        self.assertIsNone(h["round_trip_basis"])
        self.assertEqual(h["round_trip_s"], 50.0)
        self.assertTrue(any("CSV not found" in c for c in h["caveats"]))

    def test_cold_load_is_not_removed(self):
        # a long spawn->done (the model load) is not one of the two stages: the fixed value only drops the bug time
        self.write(["t_mothership,ex0,1,bighonker,0.38,1,2.0,500,4,40\n"])
        self.assertEqual(self.helper()["round_trip_s_fixed"], 40.0)


class ResidencyPrice(unittest.TestCase):
    """Design step 4: the residency price is the MEDIAN of measured cold loads per box x model (infer_ollama's
    placement record); a warm order, a pre-field order, an unreadable file say nothing - never a zero."""

    def _order(self, root, name, host, model, placement):
        import json
        import os
        d = os.path.join(root, name)
        os.makedirs(d)
        with open(os.path.join(d, "out.json"), "w", encoding="utf-8") as f:
            json.dump({"host": host, "model": model, "placement": placement, "rows": []}, f)

    def test_only_measured_cold_loads_count_and_the_median_is_taken(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        self._order(root, "a", "BigHonker", "qwen3:8b", {"cold": True, "load_s": 4.0})
        self._order(root, "b", "bighonker", "qwen3:8b", {"cold": True, "load_s": 6.0})
        self._order(root, "c", "bighonker", "qwen3:8b", {"cold": True, "load_s": 30.0})
        self._order(root, "d", "bighonker", "qwen3:8b", {"cold": False, "load_s": None})        # warm: says nothing
        self._order(root, "e", "bighonker", "qwen3:8b", {"size_vram": 1, "size": 1})            # pre-field order
        self._order(root, "f", "mothership", "qwen3.8:27b", {"cold": True, "load_s": None})     # unknown, not zero
        with open(os.path.join(root, "g.json"), "w") as f:
            f.write("{")
        prices = P.load_prices(root)
        self.assertEqual(prices, {("bighonker", "qwen3:8b"): {"load_s": 6.0, "load_n": 3}})

    def test_every_row_is_stamped_and_unmeasured_is_none(self):
        pol = {"fam": {"tiers": {"8gb": {"fastest": [{"box": "bighonker", "model": "qwen3:8b"},
                                                     {"box": "softserve", "model": "qwen3:8b"}]}}}}
        P.stamp_load(pol, {("bighonker", "qwen3:8b"): {"load_s": 6.0, "load_n": 3}})
        rows = pol["fam"]["tiers"]["8gb"]["fastest"]
        self.assertEqual([(r["load_s"], r["load_n"]) for r in rows], [(6.0, 3), (None, 0)])


class FellowshipDeskOrderOverhead(unittest.TestCase):
    """A chat turn placed on the Fellowship is a desk ORDER: before its first token it pays the desk round trip, Tolkien's
    pickup, the per-order server launch and the prompt read. proof paid 85 s on a 1.9 s row while
    the weighing charged nothing (a Fellowship row is not a card, so residency_cost had nothing to ask). The price is
    the MEDIAN over delivered hai-chat orders of (finished - submit stamp) - the turn row's own generation time; an
    undelivered, void or turn-less order says nothing - never a zero."""

    def _delivery(self, root, day, stamp, finished, gen_ms=940.0, usable=True, task="turn", box="rigby"):
        import json
        import os
        d = os.path.join(root, day)
        os.makedirs(d, exist_ok=True)
        name = "hai-fel-claudesupervisor-0c8d5333-%s-c3cd" % stamp
        rows = [{"task": "control_add", "ran": True, "gen_ms": 800.0},
                {"task": task, "ran": True, "gen_ms": gen_ms}]
        body = {"name": name + "_fellowship", "finished": finished, "usable": usable, "rows": rows}
        if box is not None:
            body["placement_row"] = {"box": box, "model": "m"}
        with open(os.path.join(d, name + ".results.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)
        with open(os.path.join(d, name + ".items.json"), "w", encoding="utf-8") as f:
            json.dump({"items": []}, f)

    def test_the_overhead_is_the_median_of_delivered_turns_minus_their_own_generation(self):
        import tempfile
        root = tempfile.mkdtemp()
        # (finished - submitted) - the turn's own generation
        self._delivery(root, "20261023", "20261023-100000", "2026-10-23 10:01:27", gen_ms=939.646)    # 86.06 s
        self._delivery(root, "20261023", "20261023-110000", "2026-10-23 11:00:40", gen_ms=2000.0)     # 38 s
        self._delivery(root, "20261024", "20261024-090000", "2026-10-24 09:02:00", gen_ms=1000.0)     # 119 s
        self._delivery(root, "20261024", "20261024-091000", "2026-10-24 09:10:30", usable=False)      # void: nothing
        self._delivery(root, "20261024", "20261024-092000", "2026-10-24 09:20:30", task="other")      # no turn: nothing
        got = P.fellowship_overheads(root)
        self.assertEqual(got, {"rigby": {"overhead_s": 86.1, "overhead_n": 3}})

    def test_each_worker_is_priced_from_its_own_deliveries(self):
        # rigby ~6 s, a tablet ~25 s (server start + model load per order) - one median hid that
        import tempfile
        root = tempfile.mkdtemp()
        self._delivery(root, "20261023", "20261023-134110", "2026-10-23 13:41:17", gen_ms=1400.0)              # 5.6
        self._delivery(root, "20261023", "20261023-134112", "2026-10-23 13:41:42", gen_ms=800.0, box="fellowship")
        self._delivery(root, "20261023", "20261023-134113", "2026-10-23 13:41:41", gen_ms=1300.0, box=None)    # says nothing
        got = P.fellowship_overheads(root)
        self.assertEqual(got, {"rigby": {"overhead_s": 5.6, "overhead_n": 1},
                               "fellowship": {"overhead_s": 29.2, "overhead_n": 1}})

    def test_deliveries_before_the_cut_over_priced_another_system_and_do_not_count(self):
        # the 08:27 proof (submitted before Tolkien's 1 s idle wait and the bare turn) waited 46 s on 30 s polls
        import tempfile
        root = tempfile.mkdtemp()
        self._delivery(root, "20261023", "20261023-082721", "2026-10-23 08:28:48", gen_ms=939.646)    # pre-fix
        self.assertEqual(P.fellowship_overheads(root), {}, "only a pre-fix delivery = unmeasured, never its old price")
        self._delivery(root, "20261023", "20261023-094000", "2026-10-23 09:40:12", gen_ms=1000.0)     # 11 s
        self.assertEqual(P.fellowship_overheads(root), {"rigby": {"overhead_s": 11.0, "overhead_n": 1}})
        self.assertEqual(P.OVERHEAD_SINCE.strftime("%Y-%m-%d %H:%M"), "2026-10-23 09:33")

    def test_no_deliveries_is_unmeasured_never_zero(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        self.assertEqual(P.fellowship_overheads(os.path.join(root, "missing")), {})
        self.assertEqual(P.fellowship_overheads(root), {})

    def test_only_fellowship_rows_are_stamped_each_with_its_own_worker_and_the_caveat_says_so(self):
        pol = {"chat-turn": {"tiers": {"fellowship": {"fastest": [
            {"box": "rigby", "model": "Qwen2.5-1.5B", "caveats": ["preliminary"]},
            {"box": "fellowship", "model": "Qwen2.5-0.5B", "caveats": []},
            {"box": "bighonker", "model": "qwen3:8b", "caveats": []}]}}}}
        P.stamp_fellowship(pol, {"rigby": {"overhead_s": 6.2, "overhead_n": 3},
                                 "fellowship": {"overhead_s": 25.0, "overhead_n": 2}})
        rows = pol["chat-turn"]["tiers"]["fellowship"]["fastest"]
        self.assertEqual([r.get("order_overhead_s") for r in rows], [6.2, 25.0, None])
        self.assertEqual([r.get("overhead_n") for r in rows], [3, 2, None])
        self.assertIn("preliminary", rows[0]["caveats"])
        self.assertTrue(any("desk order: +6.2 s before the first token" in c for c in rows[0]["caveats"]))
        self.assertEqual(rows[2]["caveats"], [], "a GPU row pays residency instead; it is not stamped")
        # the per-ORDER energy: overhead x the worker's marginal watts on rigby rows;
        # the tablet pool is unmetered (None, never 0); GPU rows carry nothing
        self.assertEqual(rows[0]["j_order"], round(6.2 * P.rigby_watts(rows[0].get("model"))[0], 1))
        self.assertTrue(any("J per order" in c for c in rows[0]["caveats"]), rows[0]["caveats"])
        self.assertIsNone(rows[1]["j_order"])
        self.assertNotIn("j_order", rows[2])
        placed = next(iter(P.menu_fill.RIGBY_PLACEMENT))
        pol2 = {"f": {"tiers": {"rigby": {"fastest": [{"box": "rigby", "model": placed, "caveats": []}]}}}}
        P.stamp_fellowship(pol2, {"rigby": {"overhead_s": 10.0, "overhead_n": 1}})
        self.assertEqual(pol2["f"]["tiers"]["rigby"]["fastest"][0]["j_order"],
                         round(10.0 * P.rigby_watts(placed)[0], 1), "a placed model's own watts, whole box")

    def test_unmeasured_stamps_none_without_a_caveat(self):
        pol = {"chat-turn": {"tiers": {"rigby": {"fastest": [{"box": "rigby", "model": "m", "caveats": []}]}}}}
        P.stamp_fellowship(pol, {})
        row = pol["chat-turn"]["tiers"]["rigby"]["fastest"][0]
        self.assertIsNone(row["order_overhead_s"])
        self.assertEqual((row["overhead_n"], row["caveats"]), (0, []))


class ReReadPrice(unittest.TestCase):
    """The re-read half of the reload price: the MEDIAN reading rate per box x model from rows that READ >= 2,000 tokens
    with the engine's own duration; a short or cache-served prompt, an errored row, a missing duration say nothing."""

    def test_only_long_reads_count_and_the_median_is_taken(self):
        import json
        import os
        import tempfile
        root = tempfile.mkdtemp()

        def order(name, host, model, rows):
            d = os.path.join(root, name)
            os.makedirs(d)
            with open(os.path.join(d, "out.json"), "w", encoding="utf-8") as f:
                json.dump({"host": host, "model": model, "rows": rows}, f)
        order("a", "BigHonker", "qwen3.5:9b", [
            {"prompt_eval_count": 4000, "prompt_eval_duration": 2e9},           # 2,000 tok/s
            {"prompt_eval_count": 8000, "prompt_eval_duration": 2.5e9},         # 3,200 tok/s
            {"prompt_eval_count": 6000, "prompt_eval_duration": 2e9},           # 3,000 tok/s
            {"prompt_eval_count": 110, "prompt_eval_duration": 1e8},            # short: says nothing
            {"prompt_eval_count": 9000, "prompt_eval_duration": None},          # no duration
            {"prompt_eval_count": 9000, "prompt_eval_duration": 1e9, "error": "x"}])
        order("b", "mothership", "qwen3.8:27b", [{"prompt_eval_count": 50, "prompt_eval_duration": 1e9}])
        with open(os.path.join(root, "c.json"), "w") as f:
            f.write("{")
        self.assertEqual(P.read_prices(root), {("bighonker", "qwen3.5:9b"): {"read_tok_s": 3000.0, "read_n": 3}})

    def test_rigby_reads_come_from_its_tolkien_results(self):
        """rigby's riser read 3.5x faster and its rows still said read_tok_s None - read_prices only knew
        ollama's out.json. Real row shape from riser-x16-c1-standard-1060.20261026 (2,052 tokens at ~350 tok/s)."""
        import json
        import os
        import tempfile
        root = tempfile.mkdtemp()
        mtp, plain = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", "Qwen2.5-7B-Instruct-Q4_K_M.gguf"

        def deliver(name, rows):
            with open(os.path.join(root, name + ".results.json"), "w", encoding="utf-8") as f:
                json.dump({"name": name, "rows": rows}, f)
        row = {"arm": "rigby", "model": mtp, "prompt_tokens": 2052, "prompt_ms": 5863.0, "cache_n": 0}   # 350 tok/s
        deliver("x16.20261026-1618.20261026-170000", [
            row, dict(row, prompt_ms=5130.0),                                     # 400
            dict(row, cache_n=1987, prompt_tokens=2052, prompt_ms=200.0),         # cache-served: says nothing
            dict(row, prompt_tokens=47, prompt_ms=100.0),                         # short
            dict(row, error="x"), dict(row, arm="warm")])
        deliver("x1.20261016-0823.20261016-085950", [dict(row, prompt_ms=20217.0)])   # 101.5 on the gone x1 riser
        deliver("cpu.20261016-0823.20261016-085950", [dict(row, model=plain, prompt_ms=20520.0)])  # no epoch: counts
        self.assertEqual(P.rigby_read_prices(root), {("rigby", mtp): {"read_tok_s": 375.0, "read_n": 2},
                                                     ("rigby", plain): {"read_tok_s": 100.0, "read_n": 1}})

    def test_every_row_is_stamped_and_unmeasured_is_none(self):
        pol = {"fam": {"tiers": {"8gb": {"fastest": [{"box": "bighonker", "model": "qwen3.5:9b"},
                                                     {"box": "softserve", "model": "qwen3:8b"}]}}}}
        P.stamp_read(pol, {("bighonker", "qwen3.5:9b"): {"read_tok_s": 2843.0, "read_n": 24}})
        rows = pol["fam"]["tiers"]["8gb"]["fastest"]
        self.assertEqual([(r["read_tok_s"], r["read_n"]) for r in rows], [(2843.0, 24), (None, 0)])


class FirstVisibleOutput(unittest.TestCase):
    """Checklist B1/B5: the windows' own per-turn records give every GPU row its time to first
    visible output on a warm window (median, new-window first turns left out); Fellowship rows are not stamped (their
    desk overhead is that price); unmeasured is None, never zero."""

    def test_the_median_of_warm_window_turns_is_stamped_on_gpu_rows_only(self):
        import json
        import os
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "chat-window-turns.json")
        json.dump({"bighonker": [{"model": "qwen3:8b", "first_s": 9.0, "first_turn": True},
                                 {"model": "qwen3:8b", "first_s": 0.8, "first_turn": False},
                                 {"model": "qwen3:8b", "first_s": 1.2, "first_turn": False},
                                 {"model": "qwen3:8b", "first_s": 0.6, "first_turn": False},
                                 {"model": "qwen3.5:9b", "first_s": None, "first_turn": False}],
                   "softserve": []}, open(p, "w", encoding="utf-8"))
        firsts = P.first_outputs(p, windows=None)
        self.assertEqual(firsts, {("bighonker", "qwen3:8b"): {"first_out_s": 0.8, "first_out_n": 3}})
        pol = {"fam": {"tiers": {"8gb": {"fastest": [{"box": "bighonker", "model": "qwen3:8b"},
                                                     {"box": "softserve", "model": "qwen3:8b"},
                                                     {"box": "rigby", "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"}]}}}}
        P.stamp_first_output(pol, firsts)
        rows = pol["fam"]["tiers"]["8gb"]["fastest"]
        self.assertEqual([(r.get("first_out_s"), r.get("first_out_n")) for r in rows], [(0.8, 3), (None, 0), (None, None)])
        self.assertEqual(P.first_outputs(p + ".missing", windows=None), {}, "no store = nothing measured")

    def test_strata_windows_price_their_own_turns_from_the_engines_records(self):
        """Kirk's store holds only ollama windows, so Flash-Next had no time to first output. Its windows' own
        out.json records carry the engine's state restore and prompt read; the first turn and an errored turn are left out,
        and an ollama window's folder is never read here (Kirk's store prices it)."""
        import json
        import os
        import tempfile
        root = tempfile.mkdtemp()

        def window(name, strata, outs):
            d = os.path.join(root, name)
            os.makedirs(d)
            json.dump({"host": "mothership", "model": "flash" if strata else "ollama-model", "strata": strata},
                      open(os.path.join(d, "WINDOW.json"), "w", encoding="utf-8"))
            for iid, o in outs.items():
                json.dump(o, open(os.path.join(d, iid + ".out.json"), "w", encoding="utf-8"))
        window("hai-win-a", True, {"t00001-x": {"prompt_eval_s": 20.0, "state_s": 0.0},
                                   "t00002-x": {"prompt_eval_s": 1.5, "state_s": 0.5},
                                   "t00003-x": {"prompt_eval_s": 1.0, "state_s": 0.0},
                                   "t00004-x": {"prompt_eval_s": 3.0, "state_s": 0.0},
                                   "t00005-x": {"prompt_eval_s": 9.0, "error": "cut"}})
        window("hai-win-b", False, {"t00002-y": {"prompt_eval_s": 99.0}})
        empty = os.path.join(root, "none.json")
        self.assertEqual(P.first_outputs(empty, windows=root), {("mothership", "flash"): {"first_out_s": 2.0, "first_out_n": 3}})


class LiveRoles(unittest.TestCase):
    """Positive control on the REAL reducer summary: the join must find the crowned lead and a metered helper."""

    def test_real_summary_gives_the_crown_and_helpers(self):
        summary, info = P.load_roles()
        if summary is None:
            self.skipTest("no phase7-roles summary reachable")
        r = P.compile_roles(summary)
        self.assertTrue(any(e["crowned"] for e in r["leads"]), "the crowned lead matched no summary arm")
        self.assertTrue(any(h["j_per_piece"] for h in r["helpers"]), "no metered helper piece")


class ContextsAreTheMeasuredFit(unittest.TestCase):
    """: policy.json carries each model's measured window per box, and HAI reads it from there."""

    def test_the_contexts_section_is_the_fit_table_itself(self):
        doc = P.contexts_doc()
        for box in ("mothership", "bighonker", "softserve"):
            self.assertEqual(doc[box], {m: int(c) for m, c in P.menu_fill.TESTED_CTX[box].items()})
        self.assertEqual(doc["bighonker"]["qwen3.5:9b"], 19968)
        self.assertIn("TESTED_CTX", doc["source"])

    def test_the_offload_list_is_menu_fills_own(self):
        # HAI's chat windows declare --offload-ok from THIS list
        doc = P.offload_ok_doc()
        self.assertEqual(sorted(map(tuple, doc)), sorted(P.menu_fill.OFFLOAD_OK))
        self.assertIn(["bighonker", "qwen3.6:35b-a3b-ud-q3kxl-mtp"], doc)

    def test_the_split_ram_side_comes_from_the_deliveries_that_ran_it_split(self):
        # live campaigns (read-only): BigHonker's A3B ran split at 65,536 on 10-17 (size 18.04 GB, 6.81 GB on the card)
        import os
        import menu_paths
        if not (menu_paths.CAMPAIGNS and os.path.isdir(menu_paths.CAMPAIGNS)):
            self.skipTest("no campaigns folder here: the lab's deliveries stay with the lab")
        ram = P.offload_ram_doc()
        a3b = ram["bighonker"]["qwen3.6:35b-a3b-ud-q3kxl-mtp"]
        self.assertGreaterEqual(a3b["n"], 2, "both 10-17 speed checks count")
        self.assertEqual(a3b["host_mib"], -(-(18040529875 - 6810668891) // 1048576))
        self.assertEqual(a3b["num_ctx"], 65536)
        self.assertTrue(all(s["sha256"] for s in a3b["sources"]))
        self.assertTrue(set(ram) <= {b for b, _m in P.menu_fill.OFFLOAD_OK}, "only declared pairs")


class MainRunsEndToEnd(unittest.TestCase):
    """adding the fourth lens broke main()'s summary print AFTER policy.json was written, so the refresh
    task failed on every run while 47 tests of the parts stayed green. Run the whole entry point on the live inputs
    (read-only) into a temp file."""

    def test_main_writes_a_table_with_every_lens_on_every_tier(self):
        import contextlib, io, json, os, tempfile
        import menu_paths
        if not (menu_paths.CAMPAIGNS and os.path.isdir(menu_paths.CAMPAIGNS)):
            # main builds from the lab's measured memory fits and deliveries, which refuse to be absent
            self.skipTest("no campaigns folder here: the measured fits and deliveries main builds from stay with the lab")
        out = os.path.join(tempfile.mkdtemp(), "policy.json")
        with contextlib.redirect_stdout(io.StringIO()):
            P.main(["--out", out])
        doc = json.load(open(out, encoding="utf-8"))
        tiers = [t for p in doc["families"].values() for t in p["tiers"].values()]
        self.assertTrue(tiers, "zero tiers written")
        for t in tiers:
            self.assertEqual(set(P.LENSES) - set(t), set())


class EmbedFamily(unittest.TestCase):
    """#34, HAI's per-turn embeds join the menu - one row per GPU box from the embed-price orders. One model
    everywhere, so quality is 1.0 by construction once a box's vectors agree with the fleet's; a box that disagrees is
    an instrument fault, refused; GPU boxes only, the 8 GB boxes home and the lead's card overflow; the family's names
    are the scheduler's."""

    V = [0.1, 0.2, 0.3, 0.4]

    def _delivery(self, root, box, stamp, walls, loop_j, vec, model="nomic-embed-text", meter="nvidia-smi@4Hz"):
        import json
        import os
        d = os.path.join(root, "embed-20261023", "embedcheck-%s-%s_%s" % (box[:2], stamp, box))
        os.makedirs(d, exist_ok=True)
        rows = [{"id": "q%02d" % (i % 3 + 1), "round": 1, "wall_s": w} for i, w in enumerate(walls)]
        body = {"host": box.upper(), "model": model, "dims": len(vec), "rows": rows,
                "vectors": {"q01": vec, "q02": [x * 2 for x in vec], "q03": [x + 1 for x in vec]},
                "summary": {"calls": len(walls), "loop_j": loop_j, "meter": meter}}
        with open(os.path.join(d, "out.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)
        return os.path.join(root, "embed-*", "*", "out.json")

    def test_rows_pool_every_delivery_per_box_quality_is_the_models_and_every_box_is_a_home(self):
        import tempfile
        root = tempfile.mkdtemp()
        pat = self._delivery(root, "bighonker", "20261023-170000", [0.07, 0.08, 0.09], 30.0, self.V)
        self._delivery(root, "bighonker", "20261023-180000", [0.05, 0.06, 0.11], 15.0, self.V)      # pooled: 6 calls, 45 J
        self._delivery(root, "softserve", "20261023-170000", [0.06, 0.06, 0.06], None, self.V, meter=None)  # unmetered
        self._delivery(root, "mothership", "20261023-170000", [0.04, 0.04, 0.05], 90.0,
                       [x * 1.0001 + 0.00001 for x in self.V])                                      # float noise
        fam = P.embed_family(P.embed_deliveries(pat), fellowship_oh={"rigby": {"overhead_s": 8.6, "overhead_n": 3}})
        rows = {e["box"]: e for e in fam["tiers"][P.EMBED_TIER]["least_energy"]}
        self.assertEqual(set(rows), {"bighonker", "softserve", "mothership"})
        bh = rows["bighonker"]
        # the WHOLE box at the wall (menu_wall's own-meter path: no window on an embed delivery), pooled over 6 calls
        wall = sum(P.menu_wall.price("bighonker", None, None, j, sum(w))["wall_j"]
                   for j, w in ((30.0, (0.07, 0.08, 0.09)), (15.0, (0.05, 0.06, 0.11))))
        self.assertEqual((bh["n"], bh["s_per_solve"], bh["j_per_solve"], bh["quality"], bh["quality_low"]),
                         (6, 0.075, round(wall / 6, 2), 1.0, 1.0))
        self.assertGreater(bh["j_per_solve"], 7.5, "more than the card's 45 J / 6 calls: the box's idle and PSU loss")
        self.assertIsNone(rows["softserve"]["j_per_solve"], "unmetered is None, never 0")
        # an embed is small work: the 8 GB boxes are its homes, the lead's one-model card is overflow with the caveat
        self.assertEqual({e["box"]: e["role"] for e in fam["tiers"][P.EMBED_TIER]["fastest"]},
                         {"bighonker": "home", "softserve": "home", "mothership": "overflow"})
        self.assertTrue(any("evicts whatever the lead holds" in c for c in rows["mothership"]["caveats"]))
        self.assertEqual((fam["fleet_fastest_s"], fam["interactive_line_s"]), (0.04, P.PRICE_X * 0.04))
        self.assertEqual(fam["refused"], [])
        self.assertIn("rigby 8.6 s", fam["note"])
        self.assertTrue(any("min cos" in c for c in bh["caveats"]), bh["caveats"])
        self.assertEqual([e["box"] for e in fam["tiers"][P.EMBED_TIER]["least_energy"]], ["bighonker", "mothership", "softserve"])
        self.assertEqual([e["box"] for e in fam["tiers"][P.EMBED_TIER]["fastest"]], ["mothership", "softserve", "bighonker"])

    def test_the_box_whose_vectors_disagree_is_refused_and_the_two_that_agree_stay(self):
        import tempfile
        root = tempfile.mkdtemp()
        pat = self._delivery(root, "bighonker", "20261023-170000", [0.07], 10.0, self.V)
        self._delivery(root, "mothership", "20261023-170000", [0.05], 10.0, self.V)
        self._delivery(root, "softserve", "20261023-170000", [0.06], 10.0, [0.4, 0.3, 0.2, 0.1])   # a different embedding
        fam = P.embed_family(P.embed_deliveries(pat))
        self.assertEqual([e["box"] for e in fam["refused"]], ["softserve"])
        self.assertIn("instrument fault", fam["refused"][0]["why"])
        self.assertEqual({e["box"] for e in fam["tiers"][P.EMBED_TIER]["fastest"]}, {"bighonker", "mothership"})

    def test_two_boxes_that_disagree_are_both_refused_nothing_says_which_is_right(self):
        import tempfile
        root = tempfile.mkdtemp()
        pat = self._delivery(root, "bighonker", "20261023-170000", [0.07], 10.0, self.V)
        self._delivery(root, "softserve", "20261023-170000", [0.06], 10.0, [0.4, 0.3, 0.2, 0.1])
        fam = P.embed_family(P.embed_deliveries(pat))
        self.assertEqual(sorted(e["box"] for e in fam["refused"]), ["bighonker", "softserve"])
        self.assertEqual(fam["tiers"][P.EMBED_TIER]["fastest"], [])
        self.assertIsNone(fam["fleet_fastest_s"])

    def test_one_box_alone_is_accepted_unchecked_and_no_deliveries_is_none(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        self.assertIsNone(P.embed_family(P.embed_deliveries(os.path.join(root, "embed-*", "*", "out.json"))))
        pat = self._delivery(root, "softserve", "20261023-170000", [0.06, 0.07], 10.0, self.V)
        bad = os.path.join(root, "embed-20261023", "embedcheck-bi-x_bighonker")
        os.makedirs(bad)
        with open(os.path.join(bad, "out.json"), "w") as f:
            f.write("{")                                                           # unreadable: says nothing
        fam = P.embed_family(P.embed_deliveries(pat))
        rows = fam["tiers"][P.EMBED_TIER]["fastest"]
        self.assertEqual([e["box"] for e in rows], ["softserve"])
        self.assertTrue(any("agreement unchecked" in c for c in rows[0]["caveats"]), rows[0]["caveats"])
        self.assertEqual(fam["refused"], [])

    def test_a_rigby_row_is_a_home_priced_by_the_plugs_marginal_watts(self):
        """The Fellowship joins the embed family by measurement - rigby's row is a
        home (not the lead's card), its J the menu's rigby rule (wall x the plug's marginal watts), and the note names it."""
        import tempfile
        root = tempfile.mkdtemp()
        pat = self._delivery(root, "bighonker", "20261023-170000", [0.03], 10.0, self.V)
        self._delivery(root, "rigby", "20261023-180000", [0.05, 0.07], None, self.V, meter=None)
        fam = P.embed_family(P.embed_deliveries(pat), fellowship_oh={"rigby": {"overhead_s": 10.2, "overhead_n": 36}})
        rows = {e["box"]: e for e in fam["tiers"][P.EMBED_TIER]["least_energy"]}
        self.assertEqual(rows["rigby"]["role"], "home")
        at = P.datetime.datetime(2026, 10, 23, 18, 0, 0).timestamp()      # the order's stamp, so the 1060 era's idle
        self.assertEqual(rows["rigby"]["j_per_solve"], round(0.06 * P.rigby_watts("nomic-embed-text", at=at)[0], 2))
        self.assertTrue(any("rigby whole box" in c for c in rows["rigby"]["caveats"]), rows["rigby"]["caveats"])
        self.assertIn("Fellowship rows priced: rigby", fam["note"])
        self.assertIn("rigby 10.2 s", fam["note"])

    def test_the_familys_names_are_the_schedulers(self):
        """menu_policy writes the family and tier; scheduler.embed_placement reads them - one seam, two trees (class 12b),
        so the two spellings are pinned against the scheduler's SOURCE (the constant has no behaviour to observe here)."""
        import re
        src = open(r"E:\SSDShared\orchestrator-build\scheduler.py", encoding="utf-8").read()
        m = re.search(r'^EMBED_FAMILY, EMBED_TIER = "([^"]+)", "([^"]+)"', src, re.M)
        self.assertIsNotNone(m, "scheduler.py no longer declares EMBED_FAMILY, EMBED_TIER on one line")
        self.assertEqual((P.EMBED_FAMILY, P.EMBED_TIER), (m.group(1), m.group(2)))


if __name__ == "__main__":
    unittest.main()
