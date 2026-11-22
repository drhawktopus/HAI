"""PLACED orders (#34 Phase 7 design § D, the "two routers"): Kirk's desk sends "the Fellowship" ONE `llm` order with
the placement key and no worker; Tolkien picks rigby or a tablet from the placement table's Fellowship rows - the first,
in the table's order, whose worker is PRESENT - and fills the spec from that row's `order` fields. Busy is a WAIT in the
lane, never a skip; a worker that is absent now leaves the order unclaimed on the desk; a key the table lists no
Fellowship row for fails with the reason. One answer runs on ONE warm node, the fastest measured."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as TF  # noqa: E402
import tolkien_llm as tv  # noqa: E402

MTP = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
RIGBY_ORDER = {"arms": ["rigby"], "model": MTP, "ctx": 32768, "temperature": 0.6, "reasoning": "off",
               "gpu": "gtx1060", "fit_target": 1024, "build": "cuda", "ubatch": 2048, "parallel": 1,
               "spec_type": "draft-mtp", "draft_max": 2, "op_offload_min_batch": 256,
               "engine_env": {"LAB_CUDA_GRAPHS_PRE_VOLTA": 1}}          # menu_fill.rigby_order_fields, verbatim shape
WARM_ORDER = {"arms": ["warm"], "model": "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", "warm_nodes": 1, "ctx": 16384,
              "temperature": 0.6}
TABLE = {"families": {"coding-f2": {"tiers": {
    "fellowship": {"least_energy": [
        {"box": "rigby", "model": MTP, "role": "home", "quality": 1.0, "s_per_solve": 4.8, "order": RIGBY_ORDER},
        {"box": "fellowship", "model": WARM_ORDER["model"], "role": "home", "quality": 0.42, "s_per_solve": 31.0,
         "order": WARM_ORDER},
        {"box": "bighonker", "model": "qwen3:8b", "role": "overflow"}]},
    "8gb": {"least_energy": [{"box": "bighonker", "model": "qwen3:8b", "role": "home"}]},
    # the rigby rung: its tablet row is OVERFLOW, ranked behind a GPU box - not the Fellowship's to take
    "rigby": {"least_energy": [
        {"box": "rigby", "model": MTP, "role": "home", "order": RIGBY_ORDER},
        {"box": "bighonker", "model": "qwen3:8b", "role": "overflow"},
        {"box": "fellowship", "model": WARM_ORDER["model"], "role": "overflow", "order": WARM_ORDER}]},
    # Spock reached the Fellowship through its overflow rows (the lead busy): the run starts at rigby's row
    "lead": {"least_energy": [
        {"box": "mothership", "model": "qwen3.8:27b", "role": "home"},
        {"box": "rigby", "model": MTP, "role": "overflow", "order": RIGBY_ORDER},
        {"box": "fellowship", "model": WARM_ORDER["model"], "role": "overflow", "order": WARM_ORDER}]}}}}}
PLACED = {"kind": "llm", "name": "a1team-piece_fellowship", "payload": "/ssdshared/p.py", "tasks": ["brief"],
          "placement": {"family": "coding-f2", "tier": "fellowship", "lens": "least_energy"},
          "requires": ["fellowship.placed"], "placed_on": "fellowship", "priority": 100}
SEEN = lambda gpu, build: (True, "")                                # the build sees the card
GONE = lambda gpu, build: (False, "%s is not visible to the %s build (expected one GPU device named 'GTX 1060' (gtx1060), found: [])" % (gpu, build))
RIGBY_CAPS = {"rigby.llm", "rigby.gt730", "fellowship.placed"}


class _Table(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="placed-")
        self.table = os.path.join(self.dir, "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(TABLE, f)
        models = os.path.join(self.dir, "models")
        cuda = os.path.join(self.dir, "cuda")
        os.makedirs(models)
        os.makedirs(cuda)
        open(os.path.join(models, MTP), "w").close()
        open(os.path.join(cuda, "llama-server"), "w").close()
        tv._POLICY.update(key=None, data=None)
        self.p = [mock.patch.object(tv, "POLICY_TABLE", self.table), mock.patch.object(tv, "MODELS", models),
                  mock.patch.dict(tv.CUDA_DIRS, {"cuda": cuda})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        tv._POLICY.update(key=None, data=None)
        shutil.rmtree(self.dir, ignore_errors=True)


class ResolvePlaced(_Table):
    def test_the_first_ready_row_fills_the_spec_and_is_recorded(self):
        out, why = tv.resolve_placed(PLACED, RIGBY_CAPS | {"tablet_cpu"}, card_check=SEEN)
        self.assertIsNone(why)
        for k, v in RIGBY_ORDER.items():
            self.assertEqual(out[k], v, k)
        self.assertEqual(out["placement_row"]["box"], "rigby")
        self.assertEqual(out["placement_row"]["skipped"], [])
        self.assertNotIn("arms", PLACED, "the desk's spec was mutated")
        self.assertEqual(tv.llm_defects(out), [], "the resolved standard 1060 order must pass intake as written")

    def test_a_card_off_the_bus_moves_the_piece_to_the_next_row(self):
        out, why = tv.resolve_placed(PLACED, RIGBY_CAPS | {"tablet_cpu"}, card_check=GONE)
        self.assertIsNone(why)
        self.assertEqual((out["arms"], out["model"], out["warm_nodes"]), (["warm"], WARM_ORDER["model"], 1))
        self.assertEqual(out["placement_row"]["skipped"][0]["box"], "rigby")
        self.assertIn("gtx1060 is not visible to the cuda build", out["placement_row"]["skipped"][0]["why"])
        self.assertEqual(tv.llm_defects(out), [])

    def test_no_worker_ready_waits_with_every_reason(self):
        out, why = tv.resolve_placed(PLACED, {"fellowship.placed"}, card_check=SEEN)
        self.assertIsNone(out)
        self.assertIn("rigby.llm is not declared", why)
        self.assertIn("no healthy tablet", why)

    def test_a_missing_model_or_build_is_absence_too(self):
        os.remove(os.path.join(tv.MODELS, MTP))
        out, _why = tv.resolve_placed(PLACED, RIGBY_CAPS | {"tablet_cpu"}, card_check=SEEN)
        self.assertIn("not in rigby's models mount", out["placement_row"]["skipped"][0]["why"])

    def test_a_key_the_table_has_no_fellowship_row_for_is_a_property_of_the_order(self):
        with self.assertRaises(tv.NoFellowshipRow):
            tv.resolve_placed(dict(PLACED, placement={"family": "coding-f2", "tier": "8gb", "lens": "least_energy"}),
                              RIGBY_CAPS, card_check=SEEN)

    def test_an_order_that_is_not_placed_or_names_its_worker_is_untouched(self):
        plain = {"kind": "llm", "name": "x", "arms": ["rigby"], "model": "m.gguf"}
        self.assertEqual(tv.resolve_placed(plain, set()), (plain, None))
        named = dict(PLACED, arms=["rigby"], model=MTP)
        self.assertEqual(tv.resolve_placed(named, set()), (named, None))

    def test_a_row_without_order_fields_is_skipped_never_guessed(self):
        t = json.loads(json.dumps(TABLE))
        for r in t["families"]["coding-f2"]["tiers"]["fellowship"]["least_energy"]:
            r.pop("order", None)
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(t, f)
        out, why = tv.resolve_placed(PLACED, RIGBY_CAPS | {"tablet_cpu"}, card_check=SEEN)
        self.assertIsNone(out)
        self.assertIn("regenerate policy.json", why)

    def test_tolkien_chooses_only_within_the_fellowships_own_run_of_the_table(self):
        """Placement is valid only within the contiguous block of the destination's own rows in the table."""
        boxes = lambda tier: [r["box"] for r in tv.policy_rows("coding-f2", tier, "least_energy")]
        self.assertEqual(boxes("fellowship"), ["rigby", "fellowship"])
        self.assertEqual(boxes("rigby"), ["rigby"])
        self.assertEqual(boxes("lead"), ["rigby", "fellowship"])
        self.assertEqual(boxes("8gb"), [])
        out, why = tv.resolve_placed(dict(PLACED, placement={"family": "coding-f2", "tier": "rigby",
                                                             "lens": "least_energy"}),
                                     RIGBY_CAPS | {"tablet_cpu"}, card_check=GONE)
        self.assertIsNone(out, "the tablets' overflow row is behind a GPU box: hand it back, never take it")
        self.assertIn("gtx1060 is not visible", why)
        out, _ = tv.resolve_placed(dict(PLACED, placement={"family": "coding-f2", "tier": "lead",
                                                           "lens": "least_energy"}),
                                   RIGBY_CAPS | {"tablet_cpu"}, card_check=GONE)
        self.assertEqual(out["arms"], ["warm"], "within the run, rigby gone -> the next Fellowship row")

    def test_the_capability_is_detected_from_a_readable_table(self):
        self.assertEqual(tv.placed_capabilities(), ["fellowship.placed"])
        self.assertEqual(tv.placed_capabilities(os.path.join(self.dir, "nope.json")), [])


class Validation(unittest.TestCase):
    def test_an_unresolved_placed_order_never_falls_to_the_legacy_default_arms(self):
        d = tv.llm_defects(PLACED)
        self.assertEqual(len(d), 1)
        self.assertIn("names no worker", d[0])

    def test_warm_nodes_is_a_positive_int_on_a_warm_order_only(self):
        base = {"kind": "llm", "name": "x", "payload": "/p.py", "tasks": ["t"]}
        self.assertEqual(tv.llm_defects(dict(base, **WARM_ORDER)), [])
        self.assertTrue(any("warm_nodes" in x for x in tv.llm_defects(dict(base, **dict(WARM_ORDER, warm_nodes=0)))))
        self.assertTrue(any("warm_nodes" in x for x in tv.llm_defects(dict(base, **dict(RIGBY_ORDER, warm_nodes=1)))))


class PickWarmNodes(unittest.TestCase):
    TABS = {"gandalf": {"gflops": 18.92}, "aragorn": {"gflops": 18.92}, "frodo": {"gflops": 17.27},
            "merry": {"gflops": 9.0}}

    def test_the_fastest_measured_nodes_first_ties_by_name(self):
        self.assertEqual(tv.pick_warm_nodes(["merry", "frodo", "gandalf", "aragorn"], 1, self.TABS), ["aragorn"])
        self.assertEqual(tv.pick_warm_nodes(["merry", "frodo"], 2, self.TABS), ["frodo", "merry"])
        self.assertEqual(tv.pick_warm_nodes([], 1, self.TABS), [])

    def test_warm_nodes_reads_only_a_real_positive_int(self):
        self.assertEqual(tv.warm_nodes({"warm_nodes": 1}), 1)
        for bad in (None, 0, -1, True, "1"):
            self.assertIsNone(tv.warm_nodes({"warm_nodes": bad}), bad)


class CollectPlaced(_Table):
    """The desk pull resolves a placed order BEFORE it claims: the queue only ever holds a spec with a worker."""

    def setUp(self):
        super().setUp()
        self.qdir = tempfile.mkdtemp(prefix="placed-q-")
        self.calls = []
        self.p2 = [mock.patch.object(TF, "QUEUE_DIR", self.qdir), mock.patch.object(TF, "P20_TOKEN", "tok"),
                   mock.patch.object(tv, "card_usable", side_effect=SEEN)]
        for x in self.p2:
            x.start()
        TF._PLACED_WAITING.clear()

    def tearDown(self):
        for x in self.p2:
            x.stop()
        shutil.rmtree(self.qdir, ignore_errors=True)
        super().tearDown()

    def _wire(self, spec):
        def fake(path, method="GET"):
            self.calls.append((method, path))
            if path.startswith("/p20/jobs?"):
                return {"jobs": [{"id": "j9", "kind": "factory_order", "order_name": spec["name"], "spec": spec}]}
            return {"ok": True}
        return mock.patch.object(TF, "_desk_call", side_effect=fake)

    def test_a_placed_order_is_staged_resolved(self):
        with self._wire(PLACED), mock.patch.object(TF, "placed_caps", return_value=RIGBY_CAPS | {"tablet_cpu"}):
            self.assertEqual(TF.collect_fleet_orders(), 1)
        with open(os.path.join(self.qdir, PLACED["name"] + ".tjob.json"), encoding="utf-8") as f:
            staged = json.load(f)
        self.assertEqual((staged["arms"], staged["model"], staged["fleet_order_id"]), (["rigby"], MTP, "j9"))
        self.assertEqual(staged["placement_row"]["box"], "rigby")

    def test_no_worker_ready_is_handed_back_to_the_desk_never_claimed(self):
        with self._wire(PLACED), mock.patch.object(TF, "placed_caps", return_value={"fellowship.placed"}), \
                mock.patch.object(TF, "log") as lg:
            self.assertEqual(TF.collect_fleet_orders(), 0)
        paths = [p for _m, p in self.calls]
        self.assertFalse([p for p in paths if "/claim" in p], "a piece we cannot serve is never claimed")
        back = [p for m, p in self.calls if m == "POST" and "/p20/jobs/j9/handback?" in p]
        self.assertEqual(len(back), 1, paths)
        self.assertIn("host=fellowship", back[0])
        self.assertEqual(os.listdir(self.qdir), [])
        self.assertTrue([c for c in lg.call_args_list if "HANDED BACK" in c.args[0]])

    def test_a_refused_hand_back_waits_and_says_so_once(self):
        def fake(path, method="GET"):
            self.calls.append((method, path))
            if path.startswith("/p20/jobs?"):
                return {"jobs": [{"id": "j9", "kind": "factory_order", "order_name": PLACED["name"], "spec": PLACED}]}
            return {"ok": False, "error": "order is mothership's"} if "/handback" in path else {"ok": True}
        with mock.patch.object(TF, "_desk_call", side_effect=fake), \
                mock.patch.object(TF, "placed_caps", return_value={"fellowship.placed"}), \
                mock.patch.object(TF, "log") as lg:
            TF.collect_fleet_orders()
            TF.collect_fleet_orders()
        self.assertFalse([p for _m, p in self.calls if "/claim" in p])
        self.assertEqual(len([c for c in lg.call_args_list if "WAITS (hand-back refused)" in c.args[0]]), 1)

    def test_no_fellowship_row_is_claimed_and_failed_with_the_reason(self):
        spec = dict(PLACED, placement={"family": "coding-f2", "tier": "8gb", "lens": "least_energy"})
        with self._wire(spec), mock.patch.object(TF, "placed_caps", return_value=RIGBY_CAPS):
            self.assertEqual(TF.collect_fleet_orders(), 0)
        paths = [p for _m, p in self.calls]
        self.assertTrue(any("/claim" in p for p in paths))
        self.assertTrue(any("/result?status=failed" in p and "no%20Fellowship%20row" in p for p in paths), paths)
        self.assertEqual(os.listdir(self.qdir), [])


class CardVisibility(unittest.TestCase):
    """Card visibility must be determined by the order's own build/device lookup, never by driver /proc files which may list removed devices."""

    def test_card_usable_is_the_launchs_own_lookup(self):
        with mock.patch.object(tv, "vulkan_device", return_value="CUDA0") as vd:
            self.assertEqual(tv.card_usable("gtx1060", "cuda"), (True, ""))
        vd.assert_called_once_with(tv.card_dir("cuda"), "gtx1060")
        err = RuntimeError("expected one GPU device named 'GTX 1060' (gtx1060), found: []")
        with mock.patch.object(tv, "vulkan_device", side_effect=err):
            ok, why = tv.card_usable("gtx1060", "cuda")
        self.assertFalse(ok)
        self.assertIn("gtx1060 is not visible to the cuda build", why)

    def test_each_card_and_build_is_checked_once_and_a_cardless_order_checks_none(self):
        calls = []

        def check(g, b):
            calls.append((g, b))
            return (g != "gtx1060", "%s gone" % g)
        self.assertEqual(tv.card_checks(dict(RIGBY_ORDER, kind="llm"), check), {("gtx1060", "cuda"): "gtx1060 gone"})
        bench = {"kind": "bench", "runs": [dict(RIGBY_ORDER), dict(RIGBY_ORDER), {"gpu_layers": 99}, {"threads": 4}]}
        calls.clear()
        self.assertEqual(tv.card_problems(bench, check), ["gtx1060 gone"])
        self.assertEqual(calls, [("gtx1060", "cuda"), ("gt730", None)], "the 730 is the default card; once each")
        calls.clear()
        self.assertEqual(tv.card_checks(dict(WARM_ORDER, kind="llm"), check), {})
        self.assertEqual(calls, [], "an order that places nothing on a card launches no lookup")


if __name__ == "__main__":
    unittest.main()
