"""#34 Phase 7 step 3: Spock orders the hosts that PASSED its hard rules by the placement table; never adds, never removes,
a pin still wins, and no family / no table = today's ranking unchanged (fail-open)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _entry(box, model, q=1.0, s=2.0):
    return {"box": box, "model": model, "think": "off", "quality": q, "s_per_solve": s}


TABLE = {"families": {"chat-c1": {"tiers": {"8gb": {
    "fastest": [_entry("softserve", "qwen3:8b", 0.65, 1.8), _entry("bighonker", "qwen3:8b", 0.65, 2.3),
                _entry("mothership", "qwen3.8:27b", 0.88, 4.7)],
    "least_energy": [_entry("bighonker", "qwen3:8b"), _entry("softserve", "qwen3:8b")],
    "best_quality": [_entry("mothership", "qwen3.8:27b", 1.0, 8.1)]}}}}}


class PolicyPlacement(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.table = os.path.join(self.dir, "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(TABLE, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.p = [mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")),
                  mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch("app.failover_available", return_value=(True, "idle")),
                  mock.patch.object(scheduler, "record_placement"),
                  mock.patch.object(config, "POLICY_TABLE", self.table)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def hosts(self, **kw):
        return [h for h, _s, _w in scheduler.place("chat", **kw)]

    def test_a_kept_loaded_advisor_is_not_bounced_by_the_ram_spill_gate(self):
        """Advisor exemption: resident models bypass the RAM spill gate, but cold ones are still gated."""
        # Residency is reported per box URL, so the stub keys on the fleet table's own entry for
        # Softserve rather than an address written in the test.
        softserve_url = (scheduler.CARDS.get(scheduler.FAILOVER) or {}).get("url")

        def loaded(url):
            return ["tev1:latest"] if url == softserve_url else []
        with mock.patch.object(scheduler, "ram_free_gb", return_value=6.7), \
                mock.patch("ollama_client.models_loaded", side_effect=loaded):
            ranked = scheduler.place("advisor")
        self.assertEqual(ranked[0][0], scheduler.FAILOVER, ranked)
        self.assertIn("spill gate skipped", ranked[0][2])
        with mock.patch.object(scheduler, "ram_free_gb", return_value=6.7), \
                mock.patch("ollama_client.models_loaded", return_value=[]):
            self.assertNotIn(scheduler.FAILOVER, [h for h, _s, _w in scheduler.place("advisor")],
                             "a model NOT resident still has to fit the RAM")

    def test_no_family_is_todays_ranking(self):
        self.assertEqual(self.hosts(), self.hosts(family=None, tier="8gb"))
        self.assertEqual(self.hosts()[0], scheduler.BRAIN, "chat's own tier order: BigHonker first")

    def test_the_table_orders_the_eligible_hosts(self):
        self.assertEqual(self.hosts(family="chat-c1", tier="8gb", lens="fastest")[:3],
                         [scheduler.FAILOVER, scheduler.BRAIN, scheduler.LEAD_BOX])
        why = scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")[0][2]
        self.assertIn("policy chat-c1/8gb/fastest #1: qwen3:8b off", why)

    def test_the_table_never_adds_a_host_the_rules_refused(self):
        with mock.patch.object(scheduler, "training_busy",
                               side_effect=lambda h: (h == scheduler.FAILOVER, "training")):
            got = self.hosts(family="chat-c1", tier="8gb", lens="fastest")
        self.assertNotIn(scheduler.FAILOVER, got)
        self.assertEqual(got[0], scheduler.BRAIN)

    def test_a_pin_still_wins(self):
        self.assertEqual(self.hosts(family="chat-c1", tier="8gb", lens="fastest", only=(scheduler.LEAD_BOX,)),
                         [scheduler.LEAD_BOX])

    def test_lens_defaults_from_urgency(self):
        self.assertEqual(self.hosts(family="chat-c1", tier="8gb", interactive=False)[0], scheduler.BRAIN,
                         "background work defaults to least_energy")

    def test_a_missing_or_broken_table_changes_nothing(self):
        base = self.hosts()
        with mock.patch.object(config, "POLICY_TABLE", os.path.join(self.dir, "nope.json")):
            scheduler._POLICY_CACHE.update(key=None, data=None)
            self.assertEqual(self.hosts(family="chat-c1", tier="8gb"), base)
        with open(self.table, "w", encoding="utf-8") as f:
            f.write("{not json")
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.assertEqual(self.hosts(family="chat-c1", tier="8gb"), base)

    def test_an_unknown_family_changes_nothing(self):
        self.assertEqual(self.hosts(family="no-such", tier="8gb"), self.hosts())

    def test_the_record_says_whether_the_table_applied(self):
        scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")
        scheduler.record_placement.assert_called()
        self.assertEqual(scheduler.record_placement.call_args.kwargs["policy"],
                         {"family": "chat-c1", "tier": "8gb", "lens": "fastest", "applied": True})


TOKEN_TABLE = {"families": {"coding-f2": {"tiers": {"8gb": {"least_energy": [
    dict(_entry("bighonker", "qwen3:8b"), temperature="0.7"),
    dict(_entry("softserve", "qwen3.5:9b"), think="xhigh", temperature="vendor")]}}}}}
PIECE = {"name": "piece", "mode": "compute", "requires": ["order.compute"],
         "args": ["items.json", "{ORDER_OUT}\\out.json", "--model", "{POLICY_MODEL}", "--num-ctx", "{POLICY_NUM_CTX}",
                  "{POLICY_THINK_ARGS}", "{POLICY_TEMPERATURE_ARGS}", "--rounds", "1"]}
CTX = {"bighonker": {"qwen3:8b": 17408}, "softserve": {"qwen3.5:9b": 7168}}


class ResidencyCost(unittest.TestCase):
    """#34 Phase 7 step 4: on the fastest lens a cold model's MEASURED load joins its seconds per solve; resident = 0;
    unmeasured or could-not-ask = 0 with a note (never charged). Home stays ahead of overflow."""
    setUp, tearDown, hosts = PolicyPlacement.setUp, PolicyPlacement.tearDown, PolicyPlacement.hosts

    def write_table(self, ss_load, bh_load):
        t = json.loads(json.dumps(TABLE))
        fast = t["families"]["chat-c1"]["tiers"]["8gb"]["fastest"]
        fast[0].update(role="home", load_s=ss_load)
        fast[1].update(role="home", load_s=bh_load)
        fast[2].update(role="overflow", load_s=40.0)
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(t, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def resident(self, by_host):
        urls = {scheduler.CARDS[h]["url"]: v for h, v in by_host.items()}
        return mock.patch("ollama_client.models_resident", side_effect=lambda url: urls.get(url))

    def test_a_warm_box_beats_a_faster_cold_one_by_its_measured_load(self):
        self.write_table(ss_load=12.0, bh_load=12.0)
        with self.resident({scheduler.FAILOVER: [], scheduler.BRAIN: ["qwen3:8b"], scheduler.LEAD_BOX: []}):
            got = scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")
        self.assertEqual([h for h, _s, _w in got][:3], [scheduler.BRAIN, scheduler.FAILOVER, scheduler.LEAD_BOX])
        self.assertIn("cold +12 s load", dict((h, w) for h, _s, w in got)[scheduler.FAILOVER])

    def test_both_cold_keeps_the_table_order(self):
        self.write_table(ss_load=12.0, bh_load=12.0)
        with self.resident({scheduler.FAILOVER: [], scheduler.BRAIN: [], scheduler.LEAD_BOX: []}):
            got = self.hosts(family="chat-c1", tier="8gb", lens="fastest")
        self.assertEqual(got[:3], [scheduler.FAILOVER, scheduler.BRAIN, scheduler.LEAD_BOX])

    def test_a_long_conversation_makes_a_cold_box_pay_its_re_read(self):
        self.write_table(ss_load=0.0, bh_load=0.0)
        t = json.load(open(self.table, encoding="utf-8"))
        fast = t["families"]["chat-c1"]["tiers"]["8gb"]["fastest"]
        fast[0]["read_tok_s"], fast[1]["read_tok_s"] = 1000.0, 1000.0
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(t, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        with self.resident({scheduler.FAILOVER: [], scheduler.BRAIN: ["qwen3:8b"], scheduler.LEAD_BOX: []}):
            short = [h for h, _s, _w in scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")]
            long_ = scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest", context_tokens=80000)
        self.assertEqual(short[:2], [scheduler.FAILOVER, scheduler.BRAIN], "no conversation: the table's order")
        self.assertEqual([h for h, _s, _w in long_][:2], [scheduler.BRAIN, scheduler.FAILOVER],
                         "80k tokens to re-read on the cold box outweigh its faster row")
        self.assertIn("+80 s re-read of 80000 tokens", dict((h, w) for h, _s, w in long_)[scheduler.FAILOVER])

    def test_an_unanswered_host_pays_its_measured_load(self):
        with mock.patch("ollama_client.models_resident", side_effect=RuntimeError("timed out")):
            cost, note = scheduler.residency_cost(scheduler.LEAD_BOX, "m", 45.0)
        self.assertEqual((cost, note), (45.0, "residency unknown (could not ask) - priced cold, cold +45 s load"))

    def test_an_unmeasured_read_rate_is_never_charged(self):
        self.assertEqual(scheduler.residency_cost.__defaults__[:2], (None, 0), "read rate defaults to unmeasured")
        with mock.patch("ollama_client.models_resident", return_value=[]):
            cost, note = scheduler.residency_cost(scheduler.BRAIN, "m", 5.0, None, 50000)
        self.assertEqual((cost, note), (5.0, "cold +5 s load, re-read unmeasured"))

    def test_an_unmeasured_load_is_never_charged_and_an_unanswered_host_is_priced_cold(self):
        """An unmeasured load incurs no penalty, preserving table order; an unresponsive host is priced at its measured cold load to avoid preferencing hung nodes."""
        self.write_table(ss_load=None, bh_load=12.0)
        with self.resident({scheduler.FAILOVER: [], scheduler.BRAIN: ["qwen3:8b"]}):   # mothership: None
            got = scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")
        whys = dict((h, w) for h, _s, w in got)
        self.assertEqual(got[0][0], scheduler.FAILOVER, "unmeasured load = no cost, so the table's order stands")
        self.assertIn("cold, load unmeasured", whys[scheduler.FAILOVER])
        self.assertIn("residency unknown (could not ask) - priced cold", whys[scheduler.LEAD_BOX])

    def test_overflow_never_jumps_home_however_cold_home_is(self):
        self.write_table(ss_load=500.0, bh_load=500.0)
        with self.resident({scheduler.FAILOVER: [], scheduler.BRAIN: [], scheduler.LEAD_BOX: ["qwen3.8:27b"]}):
            got = self.hosts(family="chat-c1", tier="8gb", lens="fastest")
        self.assertEqual(got[2], scheduler.LEAD_BOX)

    def test_other_lenses_ignore_residency(self):
        self.write_table(ss_load=12.0, bh_load=12.0)
        with self.resident({scheduler.FAILOVER: ["qwen3:8b"], scheduler.BRAIN: []}):
            got = self.hosts(family="chat-c1", tier="8gb", lens="least_energy")
        self.assertEqual(got[:2], [scheduler.BRAIN, scheduler.FAILOVER])


class PolicyTokens(unittest.TestCase):
    """The placed delegate (#34 Phase 7): the piece names no model; the row that placed the host chooses its setting."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.table = os.path.join(self.dir, "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(TOKEN_TABLE, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        caps = {"bighonker": {"order.compute", "model.qwen3:8b"}, "softserve": {"order.compute", "model.qwen3.5:9b"}}
        url = {h: (scheduler.CARDS.get(h) or {}).get("url") for h in CTX}
        self.p = [mock.patch.object(config, "POLICY_TABLE", self.table),
                  mock.patch.object(scheduler, "factory_capabilities", side_effect=lambda h: caps.get(h, set())),
                  mock.patch.object(config, "ctx_for",
                                    side_effect=lambda u, m, d: next((CTX[h].get(m, d) for h in CTX if url[h] == u), d))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def resolve(self, host, family="coding-f2", tier="8gb", spec=PIECE):
        return scheduler.resolve_policy_tokens(spec, host, family, tier, "least_energy")

    def test_the_row_that_placed_the_host_fills_the_piece(self):
        out, why = self.resolve("bighonker")
        self.assertIsNone(why)
        self.assertEqual(out["args"], ["items.json", "{ORDER_OUT}\\out.json", "--model", "qwen3:8b", "--num-ctx", "17408",
                                       "--temperature", "0.7", "--rounds", "1"])
        self.assertIn("model.qwen3:8b", out["requires"])
        self.assertEqual(out["policy_row"]["model"], "qwen3:8b")
        self.assertNotIn("model.qwen3:8b", PIECE["requires"], "the caller's template was mutated")

    def test_a_level_expands_and_vendor_temperature_adds_nothing(self):
        out, _ = self.resolve("softserve")
        self.assertEqual(out["args"][2:8], ["--model", "qwen3.5:9b", "--num-ctx", "7168", "--think-level", "xhigh"])
        self.assertNotIn("--temperature", out["args"])

    def test_refusals_never_fall_back_to_a_default_model(self):
        self.assertIn("no coding-f2/8gb/least_energy row", self.resolve("mothership")[1])
        self.assertIn("names no family/tier", self.resolve("bighonker", family=None)[1])
        with mock.patch.object(scheduler, "factory_capabilities", return_value={"order.compute"}):
            self.assertIn("does not declare model.qwen3:8b", self.resolve("bighonker")[1])
        with mock.patch.object(config, "ctx_for", return_value=None):
            self.assertIn("no tested context", self.resolve("bighonker")[1])

    def test_the_weighed_row_fills_the_piece_when_the_order_was_weighed(self):
        """An order with urgency is placed by the weighing; the row that ranked the host fills its order - never the
        lens row for the same host (one derivation for the box and its model)."""
        entry = {"box": "bighonker", "model": "qwen3:8b", "think": "off", "temperature": 0.3}
        out, why = scheduler.resolve_policy_tokens(PIECE, "bighonker", "coding-f2", "8gb", "least_energy", entry=entry)
        self.assertIsNone(why)
        self.assertIn("0.3", out["args"])
        self.assertEqual((out["policy_row"]["lens"], out["policy_row"]["temperature"]), ("weighed", 0.3))

    def test_compose_orders_weighs_once_and_fills_from_that_row(self):
        entry = {"box": "bighonker", "model": "qwen3:8b", "think": "off", "temperature": 0.3}
        seen = {}

        def place_order(requires, **kw):
            seen.update(kw)
            return {"ok": True, "placements": {"bighonker": None}, "rejected": {}, "why": []}
        with mock.patch.object(scheduler, "weighed_entries", return_value=(("B", "R"), {"bighonker": entry})) as we, \
                mock.patch.object(scheduler, "place_order", side_effect=place_order):
            res = scheduler.compose_orders(PIECE, ["order.compute"], parallel=False, kind="compute",
                                           family="coding-f2", tier="8gb", urgency="background")
        we.assert_called_once_with("coding-f2", "8gb", "background")
        self.assertEqual((seen["_weighed"], seen["_weighed_rows"]), (("B", "R"), {"bighonker": entry}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["orders"]["bighonker"]["policy_row"]["lens"], "weighed")

    def test_without_urgency_nothing_is_weighed(self):
        self.assertEqual(scheduler.weighed_entries("coding-f2", "8gb", None), (None, {}))
        self.assertEqual(scheduler.weighed_entries("coding-f2", "8gb", "soon"), (None, {}))

    def test_a_declared_split_declares_offload_and_nothing_else_does(self):
        """Only add --offload-ok if the model is explicitly allowed to split on the host; otherwise the residency check rejects it."""
        spec = dict(PIECE, args=PIECE["args"] + ["{POLICY_OFFLOAD_ARGS}"])
        with mock.patch.object(scheduler, "offload_ok", side_effect=lambda h, m: (h, m) == ("bighonker", "qwen3:8b")):
            split, _ = self.resolve("bighonker", spec=spec)
            whole, _ = self.resolve("softserve", spec=spec)
        self.assertEqual(split["args"][-1], "--offload-ok")
        self.assertNotIn("--offload-ok", whole["args"])
        self.assertNotIn("{POLICY_OFFLOAD_ARGS}", json.dumps(whole))

    def test_a_template_without_tokens_is_untouched(self):
        plain = {"name": "x", "args": ["--model", "qwen3:8b"]}
        self.assertEqual(self.resolve("bighonker", family=None, spec=plain), (plain, None))

    def test_the_default_lens_is_one_derivation(self):
        self.assertEqual(scheduler.policy_lens("chat"), "fastest" if scheduler.KINDS["chat"].get("interactive")
                         else "least_energy")
        self.assertEqual(scheduler.policy_lens("chat", "best_quality"), "best_quality")


FEL_TABLE = {"families": {"coding-f2": {"tiers": {
    "fellowship": {"least_energy": [
        dict(_entry("rigby", "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", 1.0, 4.8), role="home"),
        dict(_entry("fellowship", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", 0.42, 31.0), role="home"),
        dict(_entry("bighonker", "qwen3:8b"), role="overflow"),
        dict(_entry("softserve", "qwen3.5:9b"), role="overflow")]},
    "8gb": {"least_energy": [
        dict(_entry("bighonker", "qwen3:8b"), role="home"),
        dict(_entry("softserve", "qwen3.5:9b"), role="home"),
        dict(_entry("rigby", "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", 1.0, 4.8), role="overflow")]}}}}}
FORM = {"spec": {"kind": "llm", "name": "piece", "payload": "/ssdshared/p.py", "tasks": ["brief"], "notify": "failures"},
        "requires": ["fellowship.placed"]}


class FellowshipDestination(unittest.TestCase):
    """#34 Phase 7 design § D: an order that brings a Fellowship FORM lets the table pick "the Fellowship" as ONE
    destination (ranked by its best worker row); the desk then sends that form with the placement key, and Tolkien
    picks rigby or a tablet. Without a form nothing changes: a piece the Fellowship cannot build never goes there."""

    def setUp(self):
        PolicyPlacement.setUp(self)
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(FEL_TABLE, f)
        self.caps = {"bighonker": {"order.compute", "model.qwen3:8b"},
                     "softserve": {"order.compute", "model.qwen3.5:9b"},
                     "mothership": {"order.compute"},
                     "fellowship": {"fellowship.placed", "rigby.llm", "tablet_cpu"}}
        url = {h: (scheduler.CARDS.get(h) or {}).get("url") for h in CTX}
        self.p += [mock.patch.object(scheduler, "factory_capabilities", side_effect=lambda h: self.caps.get(h, set())),
                   mock.patch.object(config, "ctx_for", side_effect=lambda u, m, d: next(
                       (CTX[h].get(m, d) for h in CTX if url[h] == u), d)),
                   mock.patch("ollama_client.models_resident", return_value=None),
                   # The form/tier tests supply a priced-table fixture, independent of live Plex/card occupancy.
                   mock.patch.object(scheduler, "order_host_unavailable", return_value="")]
        for x in self.p[-4:]:
            x.start()

    tearDown = PolicyPlacement.tearDown

    def compose(self, tier="fellowship", form=FORM, **kw):
        kw.setdefault("lens", "least_energy")
        if form is not None:
            kw["fellowship"] = form
        return scheduler.compose_orders(PIECE, ["order.compute"], parallel=False, family="coding-f2", tier=tier, **kw)

    def test_a_fellowship_home_tier_sends_the_form_with_its_placement_key(self):
        out = self.compose()
        self.assertTrue(out["ok"], out)
        self.assertEqual(list(out["orders"]), ["fellowship"])
        spec = out["orders"]["fellowship"]
        self.assertEqual((spec["kind"], spec["name"], spec["requires"]), ("llm", "piece_fellowship", ["fellowship.placed"]))
        self.assertEqual(spec["placement"], {"family": "coding-f2", "tier": "fellowship", "lens": "least_energy"})
        self.assertNotIn("args", spec, "the GPU template must not leak into the Fellowship's order")
        self.assertNotIn("placement", FORM["spec"], "the caller's form was mutated")

    def test_the_8gb_tier_keeps_its_home_boxes_and_its_policy_tokens(self):
        out = self.compose(tier="8gb")
        self.assertEqual(list(out["orders"]), ["bighonker"])
        self.assertIn("qwen3:8b", out["orders"]["bighonker"]["args"])

    def test_without_a_form_the_fellowship_never_takes_a_gpu_piece(self):
        out = self.compose(form=None)
        self.assertTrue(out["ok"], out)
        self.assertNotIn("fellowship", out["orders"])
        self.assertIn("cannot build", out["rejected"]["fellowship"])

    def test_a_fellowship_that_cannot_build_the_form_falls_to_overflow(self):
        self.caps["fellowship"] = {"rigby.llm", "tablet_cpu"}
        out = self.compose()
        self.assertEqual(list(out["orders"]), ["bighonker"], out)
        self.assertIn("fellowship.placed", out["rejected"]["fellowship"])

    def test_an_excluded_fellowship_is_never_ranked(self):
        out = self.compose(exclude=("fellowship",))
        self.assertNotIn("fellowship", out["orders"])

    def test_place_ranks_it_by_its_best_worker_row_and_records_it(self):
        kind = scheduler.kind_for_order(PIECE, ["order.compute"])      # the band a placed piece composes with
        got = scheduler.place(kind, family="coding-f2", tier="fellowship", lens="least_energy", fellowship=True)
        self.assertEqual(got[0][0], "fellowship")
        self.assertIn("#1: rigby Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", got[0][2])
        ranked = scheduler.record_placement.call_args.kwargs["ranked"]
        self.assertEqual(ranked[0][0], "fellowship", "the placement record must show the Fellowship it chose")
        plain = scheduler.place(kind, family="coding-f2", tier="fellowship", lens="least_energy")
        self.assertNotIn("fellowship", [h for h, _s, _w in plain], "no form, no Fellowship candidate in place()")

    def test_refusals_name_the_reason(self):
        self.assertIn("single placed order",
                      scheduler.compose_orders(PIECE, ["order.compute"], parallel=True, items=["a"], family="coding-f2",
                                               tier="fellowship", fellowship=FORM)["why"][0])
        self.assertIn("family and tier",
                      scheduler.compose_orders(PIECE, ["order.compute"], parallel=False, fellowship=FORM)["why"][0])
        self.assertIn("no spec", self.compose(form={"requires": ["fellowship.placed"]})["why"][0])


def W(box, model, quality, q_low, s, j, role, think="off", load_s=8.0):
    return {"box": box, "model": model, "think": think, "quality": quality, "quality_low": q_low, "s_per_solve": s,
            "j_per_solve": j, "role": role, "load_s": load_s, "read_tok_s": 2000.0}


_LEAD = [W("mothership", "qwen3.8:27b", 1.0, 0.97, 8.1, 2764, "home", think="on", load_s=45.0),
         W("mothership", "qwen3-coder:30b", 0.78, 0.66, 1.5, 297, "home", load_s=45.0),
         W("softserve", "qwen3.5:9b", 0.79, 0.67, 4.4, 731, "overflow"),
         W("bighonker", "nemotron-3-nano:4b", 0.80, 0.68, 5.3, 616, "overflow", think="on")]
_8GB = [W("softserve", "qwen3.5:9b", 0.88, 0.78, 4.6, 798, "home"),
        W("bighonker", "nemotron-3-nano:4b", 0.80, 0.68, 5.3, 616, "home", think="on"),
        W("bighonker", "qwen3.6:35b-a3b", 0.95, 0.86, 93.5, 10710, "home", think="on"),
        W("mothership", "qwen3.8:27b", 1.0, 0.97, 8.1, 2764, "overflow", think="on", load_s=45.0)]
_TURN = [W("bighonker", "qwen3:8b", 1.0, 0.92, 1.7, 208, "home"),
         W("softserve", "qwen3:8b", 1.0, 0.85, 1.0, 297, "home"),
         W("mothership", "qwen3.8:27b", 1.0, 0.94, 3.1, 968, "overflow")]
# the Fellowship tier: rigby's row is home (a desk ORDER - its measured overhead before the first token is the table's
# order_overhead_s, 10-23), the tablet pool's row is home with that price unmeasured, BigHonker's turn row overflows
_FEL = [dict(W("rigby", "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", 1.0, 0.90, 1.9, 103, "home", load_s=None),
             order_overhead_s=85.0, overhead_n=1),
        dict(W("fellowship", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf", 1.0, 0.90, 3.4, 160, "home", load_s=None),
             order_overhead_s=None, overhead_n=0),
        dict(_TURN[0], role="overflow")]
WEIGH_TABLE = {"families": {
    "chat-c1": {"interactive_line_s": 15.0,
                "tiers": {"lead": {"fastest": _LEAD[1:], "least_energy": _LEAD[1:], "best_quality": _LEAD},
                          "8gb": {"fastest": _8GB[:2] + _8GB[3:], "least_energy": _8GB[:2], "best_quality": _8GB}}},
    "chat-turn": {"interactive_line_s": 17.0,
                  "tiers": {"8gb": {"fastest": _TURN, "least_energy": _TURN, "best_quality": _TURN},
                            "fellowship": {"fastest": _FEL, "least_energy": _FEL, "best_quality": _FEL}}}}}


class Weighing(unittest.TestCase):
    """Per-request weighing reads all three row values simultaneously. Quality need binds first, then interactive line, home, energy, and time. This process never adds or removes hosts determined by hard rules."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.table = os.path.join(self.dir, "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(WEIGH_TABLE, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.cold = set()                      # hosts whose row model is NOT resident
        def residency(host, model, load_s, read_tok_s=None, context_tokens=0, loaded=None):
            if host in self.cold:
                return float(load_s or 0), "cold +%.0f s load" % float(load_s or 0)
            return 0.0, "resident"
        self.p = [mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")),
                  mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch.object(scheduler, "residency_cost", side_effect=residency),
                  mock.patch("ollama_client.models_resident", return_value=[]),
                  mock.patch("app.failover_available", return_value=(True, "idle")),
                  mock.patch.object(scheduler, "record_placement"),
                  mock.patch.object(config, "POLICY_TABLE", self.table)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def ranked(self, family, tier, urgency="interactive", **kw):
        return scheduler.place("chat", family=family, tier=tier, urgency=urgency, **kw)

    def hosts(self, *a, **kw):
        return [h for h, _s, _w in self.ranked(*a, **kw)]

    def test_a_chat_turn_is_judged_on_time_to_visible_and_the_rest_on_the_total(self):
        """A CHAT turn is judged on time-to-first-visible-output (falling back to total time if unmeasured), while non-chat placements are judged solely on total time."""
        table = json.loads(json.dumps(WEIGH_TABLE))
        for r in table["families"]["chat-c1"]["tiers"]["8gb"]["best_quality"]:
            if r["model"] == "qwen3.6:35b-a3b":
                r["first_out_s"] = 4.0
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(table, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        with mock.patch.object(scheduler, "_window_open_cost", return_value=(0.0, None)):
            chat, _r = scheduler.weigh_rows("chat-c1", "8gb", True, window_cost=True)
            other, _r = scheduler.weigh_rows("chat-c1", "8gb", True, window_cost=False)
        a3b = next(c for c in chat if c["entry"]["model"] == "qwen3.6:35b-a3b")
        self.assertFalse(a3b["over"], a3b["notes"])
        self.assertTrue(next(c for c in other if c["entry"]["model"] == "qwen3.6:35b-a3b")["over"],
                        "a placement that does not stream to a waiting user keeps the total")
        lead = next(c for c in chat if c["entry"]["model"] == "qwen3.8:27b")
        self.assertIn("time to visible unmeasured - the line judges the total", lead["notes"])

    def test_a_window_row_the_desk_cannot_fit_is_passed_over(self):
        """The desk must skip a model if its memory profile is missing for the target box."""
        table = json.loads(json.dumps(WEIGH_TABLE))
        rows = table["families"]["chat-c1"]["tiers"]["8gb"]["best_quality"]
        fits = {"hosts": {}}
        for r in rows:
            fits["hosts"].setdefault(r["box"], {})[r["model"]] = {"points": [], "engine_version": "x"}
        victim = rows[0]
        del fits["hosts"][victim["box"]][victim["model"]]
        table["model_fits"] = fits
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(table, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        band, rejected = scheduler.weigh_rows("chat-c1", "8gb", True, window_cost=True)
        gone = [c for c in rejected if c["entry"]["model"] == victim["model"] and c["box"] == victim["box"]]
        self.assertTrue(gone and any("no measured memory profile" in n for n in gone[0]["notes"]), rejected)
        self.assertNotIn((victim["box"], victim["model"]), [(c["box"], c["entry"]["model"]) for c in band])
        band2, _r = scheduler.weigh_rows("chat-c1", "8gb", True, window_cost=False)
        self.assertIn((victim["box"], victim["model"]), [(c["box"], c["entry"]["model"]) for c in band2] +
                      [(c["box"], c["entry"]["model"]) for c in _r], "only WINDOW rows are checked")

    def test_a_declared_split_row_is_servable_without_a_resident_profile(self):
        """Declared split rows are servable without a fully-resident profile."""
        table = json.loads(json.dumps(WEIGH_TABLE))
        rows = table["families"]["chat-c1"]["tiers"]["8gb"]["best_quality"]
        fits = {"hosts": {}}
        for r in rows:
            fits["hosts"].setdefault(r["box"], {})[r["model"]] = {"points": [], "engine_version": "x"}
        victim = rows[0]
        del fits["hosts"][victim["box"]][victim["model"]]
        table["model_fits"] = fits
        table["offload_ok"] = [[victim["box"], victim["model"]]]
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(table, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.assertTrue(scheduler.offload_ok(victim["box"], victim["model"]))
        self.assertFalse(scheduler.offload_ok(victim["box"], "some-other-model"))
        band, rejected = scheduler.weigh_rows("chat-c1", "8gb", True, window_cost=True)
        self.assertFalse(any("no measured memory profile" in n for c in rejected for n in c["notes"]
                             if (c["box"], c["entry"]["model"]) == (victim["box"], victim["model"])),
                         "a declared split is not set aside for lacking a resident profile")

    def test_has_profile_is_the_one_lookup(self):
        import model_fit
        fits = {"hosts": {"bighonker": {"qwen3:8b": {"points": []}}}}
        self.assertTrue(model_fit.has_profile(fits, "bighonker", "qwen3:8b"))
        self.assertFalse(model_fit.has_profile(fits, "bighonker", "qwen3.6:35b-a3b-ud-q3kxl-mtp"))
        self.assertIn("no measured memory profile", model_fit.check(fits, "bighonker",
                      {"model": "qwen3.6:35b-a3b-ud-q3kxl-mtp", "num_ctx": 8192}, {})["why"])

    def test_the_quality_need_binds_before_energy(self):
        got = self.ranked("chat-c1", "lead")
        self.assertEqual(got[0][0], scheduler.LEAD_BOX)
        self.assertIn("weighing chat-c1/lead/interactive #1: qwen3.8:27b on", got[0][2],
                      "the 3.8 is the lead tier's quality need; the cheaper coder:30b row is below it")
        for host, _s, why in got[1:]:
            self.assertIn("below the quality band", why, (host, why))

    def test_a_cold_lead_past_the_line_still_beats_a_lower_quality_row(self):
        self.cold = {scheduler.LEAD_BOX}
        got = self.ranked("chat-c1", "lead")
        self.assertEqual(got[0][0], scheduler.LEAD_BOX, "a cold load is a wait to tell the user, never a lower tier")
        self.assertIn("past the interactive line", got[0][2])
        self.assertIn("cold +45 s load", got[0][2])

    def test_same_quality_inside_the_line_beats_home_past_it(self):
        got = self.hosts("chat-c1", "8gb")
        # need = the A3B's 0.86: softserve's 9b (0.88) and the 3.8 overflow (1.0) are in the band, nemotron (0.80) is not;
        # the A3B is home but past the 15 s line, so the overflow row that answers in time ranks above it (checklist A4)
        self.assertEqual(got, [scheduler.FAILOVER, scheduler.LEAD_BOX, scheduler.BRAIN])

    def test_a_fellowship_row_pays_its_measured_desk_order_overhead_before_the_first_token(self):
        band, _rej = scheduler.weigh_rows("chat-turn", "fellowship")
        by_box = {c["box"]: c for c in band}
        self.assertEqual(by_box["rigby"]["first_s"], 85.0)
        self.assertAlmostEqual(by_box["rigby"]["time_s"], 86.9)
        self.assertTrue(by_box["rigby"]["over"], "85 s is past the 17 s line")
        self.assertIn("+85 s desk order (round trip, launch, prompt read)", by_box["rigby"]["notes"])
        # an interactive turn goes to the overflow row that answers inside the line, home or not (checklist A4)
        self.assertEqual([c["box"] for c in band][0], "bighonker")

    def test_an_unmeasured_desk_order_overhead_is_noted_never_charged_and_never_inside_the_line(self):
        # Unmeasured overhead is treated as infinite cost (not 0) to prevent the unmeasured pool from beating all measured boxes.
        band, _rej = scheduler.weigh_rows("chat-turn", "fellowship")
        pool = next(c for c in band if c["box"] == "fellowship")
        self.assertEqual(pool["first_s"], 0.0)
        self.assertTrue(pool["over"])
        self.assertIn("desk-order overhead unmeasured - not judged inside the line", pool["notes"])
        self.assertNotEqual(band[0]["box"], "fellowship")

    def test_background_work_on_the_fellowship_tier_keeps_the_fellowship_home(self):
        # no line for a sure `job`: home before overflow, cheapest J first, so the desk order's 85 s is a price the user
        # never waits on and the Fellowship keeps the work it was priced for
        band, _rej = scheduler.weigh_rows("chat-turn", "fellowship", interactive=False)
        self.assertEqual([c["box"] for c in band], ["rigby", "fellowship", "bighonker"])
        self.assertFalse(any(c["over"] for c in band))

    def price(self, usd, band, source="placeholder"):
        return mock.patch.object(scheduler, "_price_of_the_hour", return_value={
            "usd_per_kwh": usd, "source": source, "hour": None, "curve": None, "band": band})

    def test_every_row_carries_its_cents_at_the_hours_price_and_says_which_price(self):
        with self.price(0.11, "normal"):
            band, _rej = scheduler.weigh_rows("chat-turn", "8gb")
        bh = next(c for c in band if c["box"] == "bighonker")
        self.assertAlmostEqual(bh["cents"], 208 / 3.6e6 * 0.11 * 100)
        self.assertEqual((bh["price"], bh["price_source"], bh["price_band"], bh["held"]),
                         (0.11, "placeholder", "normal", False))
        self.assertEqual([c["box"] for c in band], ["bighonker", "softserve", "mothership"], "a normal price changes no order")

    def test_the_placement_record_carries_each_rows_cents_and_the_price_source(self):
        with self.price(0.11, "normal"):
            _r, applied, record = scheduler._apply_weighing([(scheduler.BRAIN, 1.0, "")], "chat-turn", "8gb", True)
        self.assertTrue(applied)
        row = next(r for r in record if r["box"] == "bighonker")
        self.assertAlmostEqual(row["cents"], 208 / 3.6e6 * 0.11 * 100)
        self.assertEqual((row["usd_per_kwh"], row["price_source"], row["price_band"], row["held"]),
                         (0.11, "placeholder", "normal", False))

    def test_a_peak_price_weighs_energy_before_the_line_slow_and_low(self):
        with self.price(0.20, "peak", source="psp"):
            band, _rej = scheduler.weigh_rows("chat-turn", "fellowship")
        self.assertEqual([c["box"] for c in band], ["rigby", "fellowship", "bighonker"])
        self.assertIn("peak price $0.200/kWh (psp): energy weighed first", band[0]["notes"])
        self.assertFalse(any(c["held"] for c in band), "peak slows the lab; it does not hold it")

    def test_an_extreme_price_holds_auto_work_with_a_notice_that_names_the_way_out(self):
        with self.price(1.00, "extreme", source="psp"), mock.patch.object(config, "ENERGY_EXTREME_PRICE", 0.50):
            band, _rej = scheduler.weigh_rows("chat-turn", "8gb")
        self.assertTrue(band and all(c["held"] for c in band))
        self.assertIn("$1.00/kWh this hour (psp)", band[0]["hold"])
        self.assertIn("extreme line of $0.50/kWh", band[0]["hold"])
        self.assertIn("pick a Model", band[0]["hold"], "a pin still answers - his override, every user's")

    def test_cheapest_energy_then_fastest_within_the_band(self):
        got = self.ranked("chat-turn", "8gb")
        self.assertEqual([h for h, _s, _w in got], [scheduler.BRAIN, scheduler.FAILOVER, scheduler.LEAD_BOX],
                         "softserve is faster but costs more energy; both tie on quality")
        self.assertIn("208 J", got[0][2])

    def test_background_work_is_not_bound_by_the_line(self):
        got = self.ranked("chat-c1", "8gb", urgency="background")
        self.assertEqual([h for h, _s, _w in got], [scheduler.FAILOVER, scheduler.BRAIN, scheduler.LEAD_BOX],
                         "home rows first when nobody waits; the A3B is no longer 'past the line'")
        self.assertNotIn("past the interactive line", " ".join(w for _h, _s, w in got))

    def test_the_rules_still_decide_eligibility_and_a_pin_wins(self):
        with mock.patch.object(scheduler, "training_busy",
                               side_effect=lambda h: (h == scheduler.LEAD_BOX, "training")):
            self.assertNotIn(scheduler.LEAD_BOX, self.hosts("chat-c1", "lead"))
        self.assertEqual(self.hosts("chat-c1", "lead", only=(scheduler.FAILOVER,)), [scheduler.FAILOVER])

    def test_the_record_carries_every_rows_three_numbers(self):
        self.ranked("chat-c1", "8gb")
        policy = scheduler.record_placement.call_args.kwargs["policy"]
        self.assertEqual((policy["family"], policy["tier"], policy["urgency"], policy["applied"], policy["lens"]),
                         ("chat-c1", "8gb", "interactive", True, None))
        rows = policy["weighing"]
        self.assertEqual(len(rows), 4, "every table row, band and rejected")
        for r in rows:
            for k in ("host", "model", "quality", "q_low", "time_s", "j", "band", "eligible", "over_line"):
                self.assertIn(k, r)
        self.assertFalse([r for r in rows if r["model"] == "nemotron-3-nano:4b"][0]["band"])

    def test_the_lens_path_is_unchanged_without_urgency(self):
        scheduler.place("chat", family="chat-c1", tier="8gb", lens="fastest")
        policy = scheduler.record_placement.call_args.kwargs["policy"]
        self.assertEqual(policy, {"family": "chat-c1", "tier": "8gb", "lens": "fastest", "applied": True})

    def test_a_chat_turn_pays_a_new_windows_measured_open_on_a_box_without_one(self):
        """The 10-23 overflow turn took 37.9 s on a 12.0 s price: a chat turn rides a factory WINDOW, and a box with no
        window for the row's model pays a new one's MEASURED time to first output (chat_window.first_output_s)."""
        import chat_window
        base, _ = scheduler.weigh_rows("chat-c1", "8gb")
        with mock.patch.object(chat_window, "has_window", return_value=False), \
                mock.patch.object(chat_window, "first_output_s", return_value=(20.0, 3)):
            band, _ = scheduler.weigh_rows("chat-c1", "8gb", window_cost=True)
        before = {(c["box"], c["entry"]["model"]): c["time_s"] for c in base}
        gpu = [c for c in band if c["box"] not in scheduler.FELLOWSHIP_BOXES]
        self.assertTrue(gpu)
        for c in gpu:
            self.assertAlmostEqual(c["time_s"], before[(c["box"], c["entry"]["model"])] + 20.0)
            self.assertIn("+20 s new window to first output (median of 3)", c["notes"])

    def test_an_open_window_or_an_unmeasured_box_pays_nothing_and_says_which(self):
        import chat_window
        base, _ = scheduler.weigh_rows("chat-c1", "8gb")
        before = {(c["box"], c["entry"]["model"]): c["time_s"] for c in base}
        for has, seen, note in ((True, (20.0, 3), "window open"), (False, (None, 0), "not measured")):
            with mock.patch.object(chat_window, "has_window", return_value=has), \
                    mock.patch.object(chat_window, "first_output_s", return_value=seen):
                band, _ = scheduler.weigh_rows("chat-c1", "8gb", window_cost=True)
            for c in band:
                if c["box"] in scheduler.FELLOWSHIP_BOXES:
                    continue
                self.assertEqual(c["time_s"], before[(c["box"], c["entry"]["model"])])
                self.assertTrue(any(note in n for n in c["notes"]), (note, c["notes"]))

    def test_place_weighed_hands_back_the_row_that_chose_the_box(self):
        ranked, band = scheduler.place_weighed("chat", "chat-c1", "lead")
        self.assertEqual(ranked[0][0], scheduler.LEAD_BOX)
        self.assertEqual((band[0]["host"], band[0]["entry"]["model"], band[0]["entry"]["think"], band[0]["eligible"]),
                         (scheduler.LEAD_BOX, "qwen3.8:27b", "on", True))
        self.assertEqual(scheduler.record_placement.call_count, 1, "one placement, one record")

    def test_auto_needs_come_from_the_tier(self):
        self.assertEqual(scheduler.auto_needs("fellowship"), ("chat-turn", "fellowship", True, False))
        self.assertEqual(scheduler.auto_needs("8gb"), ("chat-c1", "8gb", True, False))
        self.assertEqual(scheduler.auto_needs("tool"), ("tool", "8gb", True, False))
        self.assertEqual(scheduler.auto_needs("8gb", decided=False), ("chat-c1", "lead", True, False), "unsure => the lead")
        self.assertEqual(scheduler.auto_needs("lead", kind="job", kind_confidence=0.95, line=0.9),
                         ("chat-c1", "lead", False, False), "a sure job is background")
        self.assertEqual(scheduler.auto_needs("lead", kind="job", kind_confidence=0.6, line=0.9)[2], True,
                         "an unsure kind never makes a user wait")

    def test_a_sure_coding_request_weighs_the_coding_rows(self):
        """High-confidence coding requests must be routed to the coding-family model (coding-f2), not the default lead tier."""
        self.assertEqual(scheduler.auto_needs("8gb", family="coding", family_confidence=0.95, line=0.9),
                         ("coding-f2", "8gb", True, False))
        self.assertEqual(scheduler.auto_needs("8gb", family="coding", family_confidence=0.9, line=0.9)[0], "coding-f2")

    def test_hard_coding_stays_on_the_lead(self):
        """On the lead tier the coding rows' home is qwen3-coder:30b thinking off (function-sized tasks); a request
        McCoy sends to the lead, or is unsure about, is one he judged hard - the 3.8's chat rows, as before."""
        self.assertEqual(scheduler.auto_needs("lead", family="coding", family_confidence=0.99, line=0.9),
                         ("chat-c1", "lead", True, False))
        self.assertEqual(scheduler.auto_needs("8gb", decided=False, family="coding", family_confidence=0.99,
                                              line=0.9), ("chat-c1", "lead", True, False), "unsure tier => the lead")

    def test_the_kind_line_stays_apart_from_the_tier_line(self):
        """The KIND threshold (MCCOY_THRESHOLD) is distinct from and higher than the TIER threshold.
        It must stay at 0.90; lowering it to the 0.80 tier line causes valid chats to be misclassified as jobs."""
        with mock.patch.object(config, "MCCOY_THRESHOLD", 0.90), mock.patch.object(config, "MCCOY_TIER_THRESHOLD", 0.80):
            self.assertTrue(scheduler.auto_needs("lead", kind="job", kind_confidence=0.85)[2],
                            "a job sure at 0.85 is NOT background - the kind line is 0.90")
            self.assertFalse(scheduler.auto_needs("lead", kind="job", kind_confidence=0.92)[2])

    def test_deep_is_trusted_only_at_its_line(self):
        """Area C step 3 (design § 6 step 8a): McCoy's fourth answer, depth, is acted on only at MCCOY_DEPTH_THRESHOLD;
        unsure, quick, or no answer = quick - the direction that never makes a user wait. The 10-24 replay found tev1
        sure of depth on 1 of 108 labelled rows at 0.90, so today this fires almost never and every turn logs it."""
        self.assertTrue(scheduler.auto_needs("lead", depth="deep", depth_confidence=0.95, depth_line=0.9)[3])
        self.assertFalse(scheduler.auto_needs("lead", depth="deep", depth_confidence=0.6, depth_line=0.9)[3])
        self.assertFalse(scheduler.auto_needs("lead", depth="quick", depth_confidence=0.99, depth_line=0.9)[3])
        self.assertFalse(scheduler.auto_needs("8gb")[3], "no depth answer = quick")
        with mock.patch.object(config, "MCCOY_DEPTH_THRESHOLD", 0.7):
            self.assertTrue(scheduler.auto_needs("lead", depth="deep", depth_confidence=0.75)[3],
                            "the default line is config's, re-set only from a measurement")

    def test_a_deep_turn_keeps_rows_past_the_line_in_contention(self):
        """A deep request is worth its wait: the interactive line stops rejecting rows and every other gate stays -
        quality need, home before overflow, energy, time. On the 8 GB fixture the home A3B past the 15 s line now
        ranks ahead of the 3.8 OVERFLOW row that answers in time; the quick ordering is unchanged (the test above it)."""
        quick = self.hosts("chat-c1", "8gb")
        deep = self.ranked("chat-c1", "8gb", needs={"deep": True})
        self.assertEqual(quick, [scheduler.FAILOVER, scheduler.LEAD_BOX, scheduler.BRAIN])
        self.assertEqual(deep[-1][0], scheduler.LEAD_BOX, "overflow last once the line does not bind")
        self.assertLess([h for h, _s, _w in deep].index(scheduler.BRAIN), 2)
        bh = next(w for h, _s, w in deep if h == scheduler.BRAIN)
        self.assertIn("deep: past the line stays in contention", bh)
        self.assertIn("past the interactive line", bh, "the line is still SAID, so the user is told the wait")

    def test_the_requests_needs_reach_the_placement_record(self):
        """Verify that placement records propagate the 'deep' and 'prio' flags to the policy."""
        scheduler.place_weighed("chat", "chat-c1", "lead", deep=True, prio=True)
        policy = scheduler.record_placement.call_args.kwargs["policy"]
        self.assertEqual(policy["needs"], {"deep": True, "prio": True})
        scheduler.record_placement.reset_mock()
        scheduler.place_weighed("chat", "chat-c1", "lead")
        self.assertEqual(scheduler.record_placement.call_args.kwargs["policy"]["needs"], {"deep": False, "prio": None})

    def test_other_family_answers_stay_off(self):
        """His other families name TOPICS (63 % for the full mapping, mccoy_family_score.md) - they change nothing; an
        unsure "coding" changes nothing; the Fellowship serves chat turns only."""
        for fam in ("ops", "writing", "research", "chat", "image", None):
            self.assertEqual(scheduler.auto_needs("8gb", family=fam, family_confidence=0.99, line=0.9)[0], "chat-c1")
        self.assertEqual(scheduler.auto_needs("8gb", family="coding", family_confidence=0.89, line=0.9)[0], "chat-c1")
        self.assertEqual(scheduler.auto_needs("fellowship", family="coding", family_confidence=0.99, line=0.9)[0],
                         "chat-turn")
        self.assertEqual(scheduler.auto_needs("tool", family="coding", family_confidence=0.99, line=0.9)[0], "tool")

    def test_a_table_without_the_family_changes_nothing(self):
        base = [h for h, _s, _w in scheduler.place("chat")]
        self.assertEqual(self.hosts("no-such", "8gb"), base)


EMBED_TABLE = {"families": {"embed": {"interactive_line_s": 1.0, "tiers": {"gpu": {
    "fastest": [dict(_entry("softserve", "nomic-embed-text", 1.0, 0.06), j_per_solve=18.0, role="home", load_s=1.3),
                dict(_entry("bighonker", "nomic-embed-text", 1.0, 0.07), j_per_solve=12.0, role="home", load_s=1.3),
                dict(_entry("mothership", "nomic-embed-text", 1.0, 0.05), j_per_solve=30.0, role="overflow", load_s=1.7)],
    "least_energy": [], "best_quality": []}}}}}


class EmbedFamilyPlacement(unittest.TestCase):
    """Embeds use the menu's single embed family; cheapest energy wins within the line. Excluded boxes are ignored entirely. No family maps to None."""
    setUp, tearDown = PolicyPlacement.setUp, PolicyPlacement.tearDown

    def write(self, doc):
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def urls(self, got):
        return [u for u, _m, _c in got]

    def test_inside_the_line_the_cheapest_energy_goes_first(self):
        self.write(EMBED_TABLE)
        with mock.patch("ollama_client.models_resident", return_value=["nomic-embed-text"]):
            got = scheduler.embed_placement()
        self.assertEqual(self.urls(got)[:2], [scheduler.CARDS[scheduler.BRAIN]["url"],
                                              scheduler.CARDS[scheduler.FAILOVER]["url"]])
        self.assertEqual({m for _u, m, _c in got}, {"nomic-embed-text"})
        self.assertEqual((got[0][2]["box"], got[0][2]["j"]), ("bighonker", 12.0))

    def test_no_embed_family_is_none(self):
        self.assertIsNone(scheduler.embed_placement())

    def test_an_excluded_box_is_neither_weighed_nor_asked_its_residency(self):
        self.write(EMBED_TABLE)
        asked = []
        with mock.patch("ollama_client.models_resident",
                        side_effect=lambda url: asked.append(url) or ["nomic-embed-text"]):
            got = scheduler.embed_placement(exclude=(scheduler.BRAIN,))
        bh = scheduler.CARDS[scheduler.BRAIN]["url"]
        self.assertNotIn(bh, self.urls(got))
        self.assertNotIn(bh, asked, "a sleeping box must not be dialled for its residency")
        self.assertEqual(self.urls(got)[0], scheduler.CARDS[scheduler.FAILOVER]["url"])

    def test_a_model_resident_under_its_latest_tag_is_warm(self):
        self.write(EMBED_TABLE)
        with mock.patch("ollama_client.models_resident",
                        side_effect=lambda url: ["nomic-embed-text:latest"]
                        if url == scheduler.CARDS[scheduler.FAILOVER]["url"] else []):
            got = scheduler.embed_placement()
        by_box = {c["box"]: c for _u, _m, c in got}
        self.assertIn("resident", by_box["softserve"]["notes"])
        self.assertFalse(by_box["softserve"]["over"], "warm Softserve is inside the line")
        self.assertTrue(by_box["bighonker"]["over"], "cold BigHonker pays its measured load")
        self.assertEqual(self.urls(got)[0], scheduler.CARDS[scheduler.FAILOVER]["url"],
                         "inside the line beats a cheaper-energy row past it")

    def test_a_rows_measured_first_visible_output_is_shown_and_recorded_beside_the_total(self):
        """EMBED output is not streamed to the user; therefore, it is judged on total time rather than first visible output."""
        doc = json.loads(json.dumps(EMBED_TABLE))
        doc["families"]["embed"]["tiers"]["gpu"]["fastest"][0]["first_out_s"] = 0.4       # softserve
        self.write(doc)
        with mock.patch("ollama_client.models_resident",
                        side_effect=lambda url: [] if url == scheduler.CARDS[scheduler.FAILOVER]["url"] else ["nomic-embed-text"]):
            band, _rej = scheduler.weigh_rows("embed", "gpu", True)
        by = {c["box"]: c for c in band}
        self.assertAlmostEqual(by["softserve"]["first_out_s"], 0.4 + 1.3, places=2,
                               msg="the measured first output + this turn's cold load")
        self.assertTrue(any(n.startswith("first output ~1.7 s") for n in by["softserve"]["notes"]), by["softserve"]["notes"])
        self.assertIsNone(by["bighonker"]["first_out_s"], "unmeasured is None, never 0")
        self.assertEqual([c["box"] for c in band][0], "bighonker", "warm and cheapest: the ordering is still the total's")

    def test_a_fellowship_rows_per_order_energy_joins_its_j(self):
        """Fellowship rows join `j_order` into the total J so they sort against GPU rows on per-request energy; the join is defined by the desk overhead times the worker watts."""
        doc = json.loads(json.dumps(EMBED_TABLE))
        doc["families"]["embed"]["tiers"]["gpu"]["fastest"].append(
            dict(_entry("rigby", "nomic-embed-text-v1.5-F16.gguf", 1.0, 0.015), j_per_solve=0.81, role="home",
                 order_overhead_s=10.2, j_order=550.8))
        self.write(doc)
        with mock.patch("ollama_client.models_resident", return_value=["nomic-embed-text"]), \
             mock.patch.object(scheduler, "fellowship_load", return_value={"ahead": 0, "free_nodes": 11, "why": "test"}):
            band, _rej = scheduler.weigh_rows("embed", "gpu", False)
        by = {c["box"]: c for c in band}
        self.assertAlmostEqual(by["rigby"]["j"], 551.61, places=2)
        self.assertEqual(by["rigby"]["j_order"], 550.8)
        self.assertIn("+551 J per-order launch", by["rigby"]["notes"])
        order = [c["box"] for c in band]
        self.assertGreater(order.index("rigby"), max(order.index("bighonker"), order.index("softserve")),
                           "background weighs energy: the launch puts rigby behind both 8 GB homes (overflow still sorts last)")
        self.assertIsNone(by["bighonker"]["j_order"], "a GPU row has no per-order energy")

    def test_the_embed_model_coexists_with_plex_on_softserve(self):
        self.write(EMBED_TABLE)
        with mock.patch("ollama_client.models_resident", return_value=["nomic-embed-text"]), \
             mock.patch("app.failover_available", return_value=(False, "transcoding")):
            got = scheduler.embed_placement()
        self.assertIn(scheduler.CARDS[scheduler.FAILOVER]["url"], self.urls(got))


if __name__ == "__main__":
    unittest.main()
