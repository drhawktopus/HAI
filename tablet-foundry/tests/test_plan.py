"""#36 stage planner (tolkien_plan): fitted per-class, per-type costs and the cut/order search. No tablet touched."""
import os
import sys
import unittest
from itertools import product
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tolkien_llm as tv
import tolkien_plan as tp

# per-MiB decode cost in ms per 64-token chunk: Q4_K ~11.2, Q6_K ~16.8
B = {"Q4_K": 11.2 / 64, "Q6_K": 16.8 / 64}
LIGHT = {"Q4_K": 125.0}                        # a 7B layer with no Q6_K tensors
HEAVY = {"Q4_K": 87.6, "Q6_K": 54.6}           # a 7B layer carrying Q6_K tensors


class Classes(unittest.TestCase):
    def test_hardware_classes_come_from_tolkiens_group_table(self):
        self.assertEqual(tp.device_class("gandalf"), "A")
        self.assertEqual(tp.device_class("sam"), "B", "sam is class B hardware (SKU lanes are an experiment grouping)")
        self.assertEqual(tp.device_class("merry"), "C")
        self.assertEqual(tp.device_class("rigby"), "rigby")
        self.assertIsNone(tp.device_class("mordor"))


class Fit(unittest.TestCase):
    def test_per_type_rates_are_recovered_from_mixed_stages(self):
        mixes = [{"Q4_K": 250.0}, {"Q4_K": 212.6, "Q6_K": 54.6}, {"Q4_K": 175.2, "Q6_K": 109.1}]
        samples = [("B", m, sum(v * B[t] for t, v in m.items())) for m in mixes]
        r = tp.fit_rates(samples)["B"]
        self.assertAlmostEqual(r["Q4_K"], B["Q4_K"], places=6)
        self.assertAlmostEqual(r["Q6_K"], B["Q6_K"], places=6)
        self.assertEqual(r["_n"], 3)

    def test_types_it_cannot_tell_apart_leave_only_the_blended_rate(self):
        r = tp.fit_rates([("rigby", {"Q4_K": 100.0, "Q6_K": 50.0}, 3.0)])["rigby"]
        self.assertNotIn("Q4_K", r)
        self.assertAlmostEqual(r["*"], 3.0 / 150.0)

    def test_a_type_without_its_own_rate_costs_the_blended_rate(self):
        rates = {"B": {"*": 0.2, "Q4_K": 0.1}}
        self.assertAlmostEqual(tp.unit_cost(rates, "B", {"Q4_K": 10.0, "IQ4_XS": 10.0}), 10 * 0.1 + 10 * 0.2)


class Samples(unittest.TestCase):
    def test_the_last_stage_and_rigbys_short_reads_are_left_out(self):
        mix = [LIGHT, HEAVY, LIGHT, {"Q6_K": 400.0}]          # 3 blocks + the output unit
        rows = [
            {"model": "m.gguf", "n_ubatch": 64, "n_prompt": 2048, "n_gen": 0,
             "stages": [{"dev": "frodo", "ts": 2}, {"dev": "rigby", "ts": 2}],
             "trace": {"frodo": {"median_ms": 3000.0}, "rigby": {"median_ms": 9999.0}}},
            {"model": "m.gguf", "n_ubatch": 64, "where": "rigby", "n_prompt": 2048, "n_gen": 0, "avg_ts": 10.0},
            {"model": "m.gguf", "n_ubatch": 64, "where": "rigby", "n_prompt": 4, "n_gen": 0, "avg_ts": 2.0},
            {"model": "m.gguf", "n_ubatch": 64, "where": "rigby", "n_prompt": 512, "n_gen": 0, "n_depth": 2048,
             "avg_ts": 3.0},
            {"model": "other.gguf", "n_ubatch": 64, "where": "rigby", "n_prompt": 512, "n_gen": 0, "avg_ts": 50.0},
        ]
        orig = tv.unit_mix
        tv.unit_mix = lambda p: (3, mix)
        try:
            s = tp.samples_from_rows(rows, "/models/m.gguf", ub=64)
        finally:
            tv.unit_mix = orig
        self.assertEqual([(c, round(ms, 3)) for c, _m, ms in s], [("B", round(3000.0 / 64, 3)), ("rigby", 100.0)])
        self.assertEqual(s[0][1], {"Q4_K": 212.6, "Q6_K": 54.6}, "frodo held units 0-1: one light, one heavy")


def brute(mix, rates, devices, n_chunks, ub=64):
    """Every split into contiguous runs, every injective device assignment - the answer plan() must match."""
    U, names, best = len(mix), sorted(devices), None
    for k in range(1, len(names) + 1):
        for cuts in product(range(1, U), repeat=k - 1):
            if list(cuts) != sorted(set(cuts)):
                continue
            bounds = [0] + list(cuts) + [U]
            for order in product(names, repeat=k):
                if len(set(order)) != k:
                    continue
                if "rigby" in devices and order[-1] != "rigby":
                    continue                   # the planner's rule: rigby holds the final stage
                slow = max(ub * sum(tp.unit_cost(rates, devices[d], mix[u]) for u in range(bounds[i], bounds[i + 1]))
                           for i, d in enumerate(order))
                obj = (n_chunks + k - 1) * slow
                if best is None or obj < best - 1e-6:
                    best = obj
    return best


class Plan(unittest.TestCase):
    RATES = {"B": B, "A": {"*": 0.2, "Q4_K": 0.17, "Q6_K": 0.33}, "rigby": {"*": 0.023}}

    def test_the_search_finds_the_true_optimum(self):
        mix = [HEAVY, LIGHT, HEAVY, LIGHT, LIGHT, HEAVY, {"Q6_K": 400.0}]
        devices = {"frodo": "B", "sam": "B", "gandalf": "A", "rigby": "rigby"}
        out = tp.plan(mix, self.RATES, devices, n_chunks=16)
        self.assertAlmostEqual(out["objective_ms"], brute(mix, self.RATES, devices, 16), delta=1.0)

    def test_its_stage_list_reproduces_its_cuts_through_llamas_split(self):
        mix = [HEAVY, LIGHT, HEAVY, LIGHT, LIGHT, HEAVY, LIGHT, {"Q6_K": 400.0}]
        out = tp.plan(mix, self.RATES, {"frodo": "B", "sam": "B", "rigby": "rigby"}, n_chunks=32)
        units = tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
        self.assertEqual([len(u) for u in units], [s["ts"] for s in out["stages"]])
        self.assertEqual(sum(s["ts"] for s in out["stages"]), len(mix))

    def test_a_tablets_free_memory_is_a_hard_limit(self):
        mix = [LIGHT] * 6 + [{"Q6_K": 400.0}]
        free = tp.plan(mix, self.RATES, {"frodo": "B", "rigby": "rigby"}, n_chunks=32)
        capped = tp.plan(mix, self.RATES, {"frodo": "B", "rigby": "rigby"}, n_chunks=32, mem_cap={"frodo": 130.0})
        held = {s["dev"]: s["ts"] for s in capped["stages"]}
        self.assertLessEqual(held.get("frodo", 0), 1, "130 MiB holds one 125 MiB layer, never two")
        self.assertGreaterEqual(capped["slowest_ms"], free["slowest_ms"])

    def test_a_long_prompts_kv_cache_counts_against_a_tablets_memory(self):
        mix = [LIGHT] * 6 + [{"Q6_K": 400.0}]
        kv = [0.0, 0.0, 0.0, 64.0, 0.0, 0.0, 0.0]            # one attention layer, 32K context
        short = tp.plan(mix, self.RATES, {"frodo": "B", "rigby": "rigby"}, n_chunks=32, mem_cap={"frodo": 130.0})
        long_ = tp.plan(mix, self.RATES, {"frodo": "B", "rigby": "rigby"}, n_chunks=32, mem_cap={"frodo": 130.0},
                        kv_mib=kv)
        units = lambda out: tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
        held = lambda out: [u for s, us in zip(out["stages"], units(out)) if s["dev"] == "frodo" for u in us]
        self.assertTrue(held(short), "the 4K plan uses frodo")
        self.assertNotIn(3, held(long_), "125 MiB of weights + 64 MiB of cache does not fit 130")

    def test_growth_at_long_context_counts_against_a_tablets_memory(self):
        # round 31: aragorn, 2061 MiB free, 4 layers (1596 MiB) + 33 MiB KV at 16K -> killed at 259 MiB left
        mix = [{"IQ3_XXS": 399.0}] * 4 + [{"IQ3_XXS": 399.0}] * 4 + [{"Q6_K": 400.0}]
        kv16 = [0.0, 0.0, 0.0, 33.0, 0.0, 0.0, 0.0, 33.0, 0.0]
        kv4 = [x / 4.0 for x in kv16]
        cap = {"aragorn": 2061.0 - tv.STAGE_RESERVE_MIB}
        dev = {"aragorn": "A", "rigby": "rigby"}
        rates = dict(self.RATES, A={"*": 0.023})
        held = lambda out: {s["dev"]: s["ts"] for s in out["stages"]}.get("aragorn", 0)
        long_ = tp.plan(mix, rates, dev, n_chunks=264, mem_cap=cap, kv_mib=kv16, n_ctx=16896)
        short = tp.plan(mix, rates, dev, n_chunks=64, mem_cap=cap, kv_mib=kv4, n_ctx=4352)
        self.assertLessEqual(held(long_), 3, "4 layers at 16K is the layout the low-memory killer shot")
        self.assertEqual(held(short), 4, "the 4K layouts of rounds 14-27 still fit")
        self.assertTrue(tv.stage_memory_defects({"aragorn": tv.stage_need_mib(1596, 33, 16896)}, {"aragorn": 2061}))
        self.assertEqual(tv.stage_memory_defects({"aragorn": tv.stage_need_mib(1596, 8, 4352)}, {"aragorn": 2061}), [])

    def test_an_attention_layers_prompt_cost_moves_it_to_where_it_is_cheap(self):
        mix = [LIGHT] * 6 + [{"Q6_K": 400.0}]
        kv = [0.0, 0.0, 0.0, 64.0, 0.0, 0.0, 0.0]
        dev = {"frodo": "B", "sam": "B", "gimli": "B", "rigby": "rigby"}
        base = tp.plan(mix, self.RATES, dev, n_chunks=512, kv_mib=kv)
        out = tp.plan(mix, self.RATES, dev, n_chunks=512, kv_mib=kv, attn_ms={"B": 1e6, "rigby": 0.0})
        units = tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
        on = {u: s["dev"] for s, us in zip(out["stages"], units) for u in us}
        self.assertEqual(on[3], "rigby", "an attention layer that costs a tablet far more goes to rigby")
        self.assertGreaterEqual(out["slowest_ms"], base["slowest_ms"])

    def test_shared_rigby_keeps_costly_attention_layers_and_the_output(self):
        mix = [LIGHT] * 7 + [{"Q6_K": 400.0}]
        kv = [0.0, 0.0, 0.0, 64.0, 0.0, 0.0, 0.0, 0.0]
        dev = {"frodo": "B", "sam": "B", "gimli": "B"}
        out = tp.plan_shared(mix, self.RATES, dev, n_chunks=512, kv_mib=kv, attn_ms={"B": 1e6, "rigby": 0.0})
        units = tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
        on = {u: s["dev"] for s, us in zip(out["stages"], units) for u in us}
        self.assertTrue(tv.is_rigby(on[3]), "the attention layer stays on rigby")
        self.assertEqual(on[len(mix) - 1], "rigby", "the output unit is on the stage named rigby")
        self.assertTrue(any(not tv.is_rigby(d) for d in on.values()), "tablets take the linear layers")
        self.assertEqual(sum(s["ts"] for s in out["stages"]), len(mix))
        self.assertLess(out["slowest_ms"], out["rigby_alone_ms_per_chunk"])

    def test_writing_and_depth_stage_rows_never_set_reading_rates(self):
        base = {"model": "m.gguf", "n_ubatch": 64, "stages": [{"dev": "frodo", "ts": 1}, {"dev": "rigby", "ts": 2}],
                "trace": {"frodo": {"median_ms": 640.0}}}
        reading = dict(base, n_prompt=4096, n_gen=0)
        writing = dict(base, n_prompt=0, n_gen=128, n_depth=4096, trace={"frodo": {"median_ms": 20.0}})
        with mock.patch.object(tv, "unit_mix", return_value=(2, [LIGHT, LIGHT, {"Q6_K": 1.0}])):
            got = tp.samples_from_rows([reading, writing], "/models/m.gguf", ub=64)
        self.assertEqual(len(got), 1, "round 18: a writing row's per-token stage time is not a reading rate")
        self.assertAlmostEqual(got[0][2], 10.0)

    def test_no_plan_leaves_the_output_alone_on_the_last_stage(self):
        mix = [LIGHT] * 7 + [{"Q6_K": 400.0}]
        dev = {"frodo": "B", "sam": "B", "gimli": "B", "boromir": "B"}
        for out in (tp.plan_shared(mix, self.RATES, dev, n_chunks=512),
                    tp.plan_shared(mix, self.RATES, dev, n_chunks=512, max_stages=40),
                    tp.plan(mix, self.RATES, dict(dev, rigby="rigby"), n_chunks=512)):
            units = tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
            self.assertGreaterEqual(len(units[-1]), 2, out["stages"])

    def test_shared_rigby_never_plans_more_stages_than_ggml_can_schedule(self):
        mix = [LIGHT] * 12 + [{"Q6_K": 400.0}]
        dev = {d: "B" for d in ("frodo", "sam", "gimli", "legolas", "boromir", "faramir")}
        free = tp.plan_shared(mix, self.RATES, dev, n_chunks=512, max_stages=40)
        capped = tp.plan_shared(mix, self.RATES, dev, n_chunks=512, max_stages=5)
        self.assertGreater(free["n_stages"], 5, "the uncapped plan wants more stages - so the cap binds")
        self.assertLessEqual(capped["n_stages"], 5)
        self.assertEqual(sum(s["ts"] for s in capped["stages"]), len(mix))
        self.assertTrue(tp.tf.spec_defects({"kind": "bench", "name": "b", "runs": [{
            "id": "x", "where": "rigby", "build": "next", "model": "m.gguf", "p": [64],
            "stages": [{"dev": "rigby%d" % k if k > 1 else "rigby", "ts": 1} for k in range(1, 17)]}]}))

    def test_shared_rigby_pace_is_rigbys_total_not_its_largest_stage(self):
        mix = [LIGHT] * 7 + [{"Q6_K": 400.0}]
        out = tp.plan_shared(mix, self.RATES, {"frodo": "B", "sam": "B"}, n_chunks=512)
        units = tv.stage_units([s["ts"] for s in out["stages"]], len(mix) - 1)
        on_rigby = [u for s, us in zip(out["stages"], units) if tv.is_rigby(s["dev"]) for u in us]
        self.assertGreater(sum(1 for s in out["stages"] if tv.is_rigby(s["dev"])), 1, "rigby on both sides")
        want = sum(64 * tp.unit_cost(self.RATES, "rigby", mix[u]) for u in on_rigby)
        self.assertAlmostEqual(out["rigby_total_ms"], want, delta=1, msg="one CPU: the SUM of rigby's stages")
        self.assertGreaterEqual(out["slowest_ms"], out["rigby_total_ms"])

    def test_rigby_holds_the_final_stage_and_only_it(self):
        mix = [LIGHT] * 6 + [HEAVY] * 4 + [{"Q6_K": 400.0}]
        out = tp.plan(mix, self.RATES, {"frodo": "B", "sam": "B", "gandalf": "A", "rigby": "rigby"}, n_chunks=64)
        self.assertEqual(out["stages"][-1]["dev"], "rigby", "round 13: the output alone on a tablet failed")
        self.assertEqual([s["dev"] for s in out["stages"]].count("rigby"), 1)
        free = tp.plan(mix, self.RATES, {"frodo": "B", "sam": "B"}, n_chunks=64)
        self.assertNotIn("rigby", [s["dev"] for s in free["stages"]], "no rigby, no rule")

    def test_fill_can_make_fewer_stages_the_better_plan(self):
        mix = [LIGHT] * 4 + [{"Q6_K": 10.0}]
        few = tp.plan(mix, self.RATES, {"frodo": "B", "sam": "B", "gimli": "B", "rigby": "rigby"}, n_chunks=1)
        self.assertEqual(few["n_stages"], 1, "one chunk: a pipeline only adds fill, rigby alone wins")


if __name__ == "__main__":
    unittest.main()
