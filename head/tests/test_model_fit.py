"""Fit before load: real 9B refusal, replacement accounting, and ALL placement/start paths."""
import json
import unittest
from unittest import mock

import model_fit as F


RULES = {"full_margin_mib": 1024, "shared_min_mib": 512}
SOURCE = {"path": "fit-20261019-desktop/fit.json", "sha256": "fixture-source-hash"}
# Verbatim readings from the desktop-aware 9B order; the current 19,968 window must pass beside its 594 MiB desktop.
FIT = {"box": "bighonker", "measured_at": "2026-10-19 20:19:41", "rows": [
    {"model": "qwen3.5:9b", "num_ctx": 39936, "size": 6226465258, "size_vram": 6226465258, "fits": False,
     "spill": {"runners": 2, "adapter_mb": 7589, "shared_mb": 696, "card_mb": 8192, "dedicated_mb": 6990}},
    {"model": "qwen3.5:9b", "num_ctx": 19968, "size": 5776626154, "size_vram": 5776626154, "fits": True,
     "spill": {"runners": 2, "adapter_mb": 7159, "shared_mb": 676, "card_mb": 8192, "dedicated_mb": 6560}}]}


def profiles():
    return F.compile_profiles([(SOURCE, FIT), (dict(SOURCE, path="softserve/fit.json"), dict(FIT, box="softserve"))], RULES)


def sample(ambient=594, owned=None):
    mem = {"counter_ok": True, "card_mb": 8192, "adapter_mb": None, "dedicated_mb": None, "shared_mb": None}
    if owned is not None:
        mem.update(adapter_mb=ambient + owned, dedicated_mb=owned, shared_mb=676)
    return {"vram_used_mb": ambient, "gpu_memory": mem, "ollama_version": "0.40.5", "stale": False}


def order(ctx=39936):
    return {"mode": "compute", "load": "gpu", "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
            "args": ["items.json", "out.json", "--model", "qwen3.5:9b", "--num-ctx", str(ctx)]}


class MemoryArithmetic(unittest.TestCase):
    def check(self, ctx=39936, rec=None, **kw):
        return F.check(profiles(), "bighonker", {"model": "qwen3.5:9b", "num_ctx": ctx},
                       rec if rec is not None else sample(), **kw)

    def test_the_real_39936_order_is_refused_before_loading(self):
        r = self.check()
        self.assertFalse(r["ok"])
        self.assertGreater(r["predicted_mib"], r["full_line_mib"])
        self.assertIn("does not fit before load", r["why"])

    def test_the_new_19968_window_fits_beside_the_same_desktop(self):
        self.assertTrue(self.check(19968)["ok"])

    def test_replacing_our_loaded_model_does_not_count_it_twice(self):
        r = self.check(19968, sample(594, owned=6560))
        self.assertTrue(r["ok"])
        self.assertEqual(r["other_mib"], 594)

    def test_a_different_desktop_occupancy_changes_the_verdict(self):
        self.assertFalse(self.check(19968, sample(1500))["ok"])

    def test_a_successful_query_with_no_runner_uses_the_existing_card_reading(self):
        self.assertTrue(self.check(19968, sample())["ok"])

    def test_an_unreadable_counter_never_means_free(self):
        self.assertFalse(self.check(19968, dict(sample(), gpu_memory=None))["ok"])
        self.assertFalse(self.check(19968, dict(sample(), gpu_memory={"counter_ok": False}))["ok"])

    def test_stale_or_missing_card_reports_are_held(self):
        self.assertFalse(self.check(19968, fresh=False)["ok"])
        self.assertFalse(self.check(19968, dict(sample(), stale=True))["ok"])

    def test_engine_change_requires_new_evidence(self):
        self.assertFalse(self.check(19968, dict(sample(), ollama_version="new-version"))["ok"])

    def test_no_extrapolation_beyond_measured_windows(self):
        self.assertFalse(self.check(50000)["ok"])
        self.assertEqual(self.check(14336)["measured_num_ctx"], 19968)

    def test_unknown_model_has_no_invented_footprint(self):
        self.assertFalse(F.check(profiles(), "bighonker", {"model": "unknown", "num_ctx": 1}, sample())["ok"])

    def test_full_margin_is_not_a_universal_ceiling(self):
        # Mothership qwen3.8:27b @125952 really fitted at 24,222 MiB adapter / 452 shared, ABOVE the full line.
        p = {"rules": RULES, "hosts": {"mothership": {"q38": {"engine_version": "0.40.5", "points": [
            {"num_ctx": 125952, "required_mib": 22957, "shared_mib": 452, "card_mib": 24560}]}}}}
        rec = {"ollama_version": "0.40.5", "gpu_memory": {"counter_ok": True, "card_mb": 24560,
               "adapter_mb": 24222, "dedicated_mb": 22957, "shared_mb": 452}}
        self.assertTrue(F.check(p, "mothership", {"model": "q38", "num_ctx": 125952}, rec)["ok"])
        rec["gpu_memory"]["adapter_mb"] = 26000
        self.assertFalse(F.check(p, "mothership", {"model": "q38", "num_ctx": 125952}, rec)["ok"])

    def test_the_window_steps_down_to_the_largest_measured_point_that_fits(self):
        # Return the largest measured window that fits; do not return None if a smaller measured point is available.
        n, v = F.fit_ctx(profiles(), "bighonker", "qwen3.5:9b", 39936, sample())
        self.assertEqual(n, 19968)
        self.assertTrue(v["ok"])
        self.assertEqual(F.fit_ctx(profiles(), "bighonker", "qwen3.5:9b", 19968, sample())[0], 19968, "full when it fits")

    def test_no_point_fits_is_none_and_could_not_tell_is_not_a_step_down(self):
        n, v = F.fit_ctx(profiles(), "bighonker", "qwen3.5:9b", 39936, sample(1500))
        self.assertIsNone(n)
        self.assertIn("does not fit before load", v["why"])
        n, v = F.fit_ctx(profiles(), "bighonker", "qwen3.5:9b", 39936, sample(), fresh=False)
        self.assertEqual(n, 39936, "a missing report is the desk's to refuse at open, not a reason to shrink")
        self.assertFalse(v["ok"])

    def test_the_live_2026_10_25_bighonker_case(self):
        # verbatim: nemotron-3-nano:4b's measured points and the card's 1,061 MiB with no runner (a locked desktop)
        p = {"rules": RULES, "hosts": {"bighonker": {"nemotron-3-nano:4b": {"engine_version": "0.40.5", "points": [
            {"num_ctx": 229376, "required_mib": 5759, "shared_mib": 598, "card_mib": 8192},
            {"num_ctx": 262144, "required_mib": 6191, "shared_mib": 630, "card_mib": 8192}]}}}}
        n, v = F.fit_ctx(p, "bighonker", "nemotron-3-nano:4b", 262144, sample(1061))
        self.assertEqual((n, v["predicted_mib"]), (229376, 6820))
        self.assertEqual(F.fit_ctx(p, "bighonker", "nemotron-3-nano:4b", 262144, sample(330))[0], 262144,
                         "an emptier desktop (10-22's ~330 MiB) gets the full window")

    def test_a_window_that_fit_needs_its_own_measured_allocation_not_the_largest_windows_overhead(self):
        rows = [({"box": "mothership", "measured_at": "2026-10-16 04:21:53"},
                 {"model": "q38", "num_ctx": 122880, "size": 17918014257, "size_vram": 17918014257, "fits": True,
                  "load_s": 45.3, "spill": {"runners": 2, "adapter_mb": 24229, "shared_mb": 318, "card_mb": 24560,
                                            "dedicated_mb": 22964}}),
                ({"box": "mothership", "measured_at": "2026-10-22 11:04:05"},
                 {"model": "q38", "num_ctx": 156672, "size": 18091018812, "size_vram": 18091018812, "fits": True,
                  "load_s": 42.0, "spill": {"runners": 2, "dedicated_mb": 23822, "card_mb": 24560, "shared_mb": 384,
                                            "adapter_mb": 24232, "counter_ok": True}}),
                ({"box": "mothership", "measured_at": "2026-10-22 11:04:05"},
                 {"model": "q38", "num_ctx": 188416, "size": 20078321988, "size_vram": 17298169527, "fits": False,
                  "load_s": 44.2, "spill": {"runners": 2, "dedicated_mb": 23759, "card_mb": 24560, "shared_mb": 527,
                                            "adapter_mb": 24169, "counter_ok": True}})]
        docs = [(dict(SOURCE, path=str(i)), dict(meta, rows=[row])) for i, (meta, row) in enumerate(rows)]
        pts = {p["num_ctx"]: p["required_mib"] for p in
               F.compile_profiles(docs, RULES)["hosts"]["mothership"]["q38"]["points"]}
        self.assertEqual((pts[122880], pts[156672]), (22964, 23822), "a fitted window is its own measurement")
        self.assertGreater(pts[188416], 23759, "a spilled window still takes the size + largest-overhead estimate")
        p = F.compile_profiles(docs, RULES)
        rec = {"ollama_version": "0.40.5", "gpu_memory": {"counter_ok": True, "card_mb": 24560,
               "adapter_mb": 1222, "dedicated_mb": 0, "shared_mb": 0}}
        n, v = F.fit_ctx(p, "mothership", "q38", 188416, rec)
        self.assertEqual((n, v["ok"], v["predicted_mib"]), (122880, True, 24186))

    def test_profiles_retain_source_and_unproven_offload_is_not_a_fit(self):
        p = profiles()["hosts"]["bighonker"]["qwen3.5:9b"]
        self.assertEqual(p["sources"], [SOURCE])
        bad = dict(FIT, rows=[dict(FIT["rows"][0], fits=True, size_vram=1)])
        self.assertEqual(F.compile_profiles([(SOURCE, bad)], RULES)["hosts"], {})


class OrderRequirement(unittest.TestCase):
    def test_existing_windows_and_windows_paths_are_read_without_changing_them(self):
        self.assertEqual(F.requirement(order()), {"model": "qwen3.5:9b", "num_ctx": 39936})

    def test_triage_uses_its_declared_host_defaults(self):
        self.assertEqual(F.requirement({"payload": "x/triage_classify.py"},
                                      ["triage.model.qwen3.5:9b", "triage.ctx.19968"]),
                         {"model": "qwen3.5:9b", "num_ctx": 19968})

    def test_an_opaque_plan_has_explicit_metadata(self):
        self.assertEqual(F.requirement({"model_memory": {"model": "q38", "num_ctx": 122880}}),
                         {"model": "q38", "num_ctx": 122880})
        missing = F.requirement({"payload": "x/a1_order.py", "mode": "compute", "load": "gpu"})
        self.assertEqual(missing, {"model": None, "num_ctx": None})

    def test_metadata_cannot_hide_the_model_or_window_in_the_actual_arguments(self):
        spec = dict(order(), model_memory={"engine": "strata", "model": "smaller", "num_ctx": 1})
        self.assertEqual(F.requirement(spec), {"model": "qwen3.5:9b", "num_ctx": 39936})

    def test_a_missing_window_is_unknown_not_a_guessed_context(self):
        spec = order(); spec["args"] = spec["args"][:-2]
        self.assertIsNone(F.requirement(spec)["num_ctx"])

    def test_cpu_fit_probe_and_intentional_offload_keep_their_protocols(self):
        for spec in (dict(order(), load="cpu"), {"payload": "fit_probe.py"},
                     dict(order(), args=order()["args"] + ["--offload-ok"]),
                     dict(order(), args=order()["args"] + ["--strata", "plan.json"]),
                     {"model_memory": {"engine": "strata", "model": "fn", "num_ctx": 262144}}):
            self.assertIsNone(F.requirement(spec))


class PlacementPaths(unittest.TestCase):
    def run_place(self, ranked, **kw):
        import scheduler as S
        caps = {"order.compute", "model.qwen3.5:9b"}
        patches = (mock.patch.object(S, "place", return_value=ranked),
                   mock.patch.object(S, "factory_capabilities", side_effect=lambda h: caps if h in ("bighonker", "softserve") else set()),
                   mock.patch.object(S, "gpu_health", return_value=(True, "healthy")),
                   mock.patch.object(S, "user_on_card", return_value=("clear", "idle")),
                   mock.patch.object(S, "plex_transcoding", return_value=(False, 0, "idle")),
                   mock.patch.object(S, "_plex_gate", return_value=(True, "idle")),
                   mock.patch.object(S, "policy_data", return_value={"model_fits": profiles()}),
                   mock.patch.object(S, "gpu_load", side_effect=lambda h: sample(594 if h == "bighonker" else 0)),
                   mock.patch.object(S, "_report_age_s", return_value=1))
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in patches: stack.enter_context(p)
            return S.place_order(["order.compute", "model.qwen3.5:9b"], kind="render_small",
                                 order_spec=order(), **kw)

    def test_a_free_ranked_host_is_checked_not_only_the_busy_fallback(self):
        r = self.run_place([("bighonker", 2, "free"), ("softserve", 1, "free")])
        self.assertEqual(list(r["placements"]), ["softserve"])
        self.assertIn("model fit:", r["rejected"]["bighonker"])

    def test_busy_fallback_uses_the_same_model_fit(self):
        self.assertEqual(list(self.run_place([])["placements"]), ["softserve"])

    def test_parallel_readd_does_not_reintroduce_a_nonfitting_host(self):
        r = self.run_place([("softserve", 1, "free")], parallel=True, items=["a", "b"])
        self.assertEqual(list(r["placements"]), ["softserve"])
        self.assertIn("model fit:", r["rejected"]["bighonker"])

    def test_a_pin_is_held_for_its_box_instead_of_overridden(self):
        r = self.run_place([], only=["bighonker"])
        self.assertFalse(r["ok"])
        self.assertTrue(r["held"])


class AvailabilityTransport(unittest.TestCase):
    def test_factory_start_uses_its_current_snapshot_not_the_last_report(self):
        import scheduler as S
        with mock.patch.object(S, "factory_capabilities", return_value=set()), \
             mock.patch.object(S, "gpu_load", return_value=sample(164, owned=0)), \
             mock.patch.object(S, "_report_age_s", return_value=1), \
             mock.patch.object(S, "policy_data", return_value={"model_fits": profiles()}):
            self.assertTrue(S.model_fit_verdict("bighonker", order())["ok"])
            local = {"gpu_memory": sample(1500, owned=0)["gpu_memory"]}
            now = S.model_fit_verdict("bighonker", order(), local)
            self.assertFalse(now["ok"])
            self.assertEqual(now["other_mib"], 1500)
            self.assertFalse(S.model_fit_verdict("bighonker", order(), {"gpu_memory": None})["ok"])
            empty = dict(sample()["gpu_memory"], ambient_mb=594, ambient_age_s=0)
            self.assertTrue(S.model_fit_verdict("bighonker", order(19968), {"gpu_memory": empty})["ok"])
            empty["ambient_age_s"] = S.GPU_LOAD_FRESH_S + 1
            self.assertFalse(S.model_fit_verdict("bighonker", order(19968), {"gpu_memory": empty})["ok"])

    def test_a_different_serving_host_cannot_use_this_factorys_capacity_check(self):
        import scheduler as S
        spec = dict(order(), args=order()["args"] + ["--host", "http://10.0.0.76:11434"])
        r = S.model_fit_verdict("bighonker", spec)
        self.assertFalse(r["ok"])
        self.assertIn("not this factory", r["why"])

    def test_a_plex_measurement_lifts_only_plex_not_the_fit_rule(self):
        import scheduler as S
        spec = dict(order(), coexists_with_plex=True)
        with mock.patch.object(S, "gpu_health", return_value=(True, "healthy")), \
             mock.patch.object(S, "user_on_card", return_value=("clear", "Plex is accounted")), \
             mock.patch.object(S, "plex_transcoding", return_value=(True, 1, "transcoding")):
            r = S.order_host_unavailable("softserve", "render_small", spec,
                                         {"ok": False, "why": "model fit: no room"})
        self.assertEqual(r, "model fit: no room")

    def test_factory_spec_is_passed_to_the_same_availability_judgement(self):
        import app
        import scheduler as S
        fit = {"ok": False, "why": "model fit: no room"}
        with mock.patch.object(S, "gpu_health", return_value=(True, "healthy")), \
             mock.patch.object(S, "model_fit_verdict", return_value=fit) as calc, \
             mock.patch.object(S, "order_host_unavailable", return_value=fit["why"]) as availability, \
             mock.patch.object(S, "user_on_card", return_value=("clear", "idle")):
            r = app._fleet_availability_for("bighonker", order())
        calc.assert_called_once_with("bighonker", order())
        availability.assert_called_once_with("bighonker", "render_small", order(), fit)
        self.assertFalse(r["start_ok"])
        self.assertEqual(r["model_fit"], fit)

    def test_gpu_memory_survives_the_report_boundary(self):
        import app
        import scheduler as S
        rec = sample()
        req = app.GpuLoadReq(host="bighonker", util_pct=0, gpu_memory=rec["gpu_memory"], ollama_version="0.40.5")
        with mock.patch.object(S, "report_gpu_load", return_value={"ts": "fixture-time"}) as push:
            app.fleet_gpu_load(req)
        self.assertEqual(push.call_args.args[-2:], (rec["gpu_memory"], "0.40.5"))
