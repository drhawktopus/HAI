"""`bench` order kind (#36): llama-bench on rigby or a tablet, and a tablet's USB link. No process, socket or
tablet is touched."""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf
import tolkien_llm as tv

GOOD = {"kind": "bench", "name": "b1", "runs": [
    {"id": "rigby-7b", "where": "rigby", "model": "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "p": [1, 2, 4, 8, 512],
     "n": [64], "d": [0, 2048]},
    {"id": "rigby-7b-card", "where": "rigby", "model": "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "p": [512],
     "gpu_layers": 8, "op_offload": False},
    {"id": "gandalf-1.5b", "where": "gandalf", "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "p": [512]},
    {"id": "gandalf-link", "where": "gandalf", "link_mb": 64}]}


_STATE = mock.patch.object(tf, "STATE_DIR", None)


def setUpModule():
    import tempfile
    _STATE.new = tempfile.mkdtemp(prefix="bench_state_")
    _STATE.start()


def tearDownModule():
    import shutil
    _STATE.stop()
    shutil.rmtree(_STATE.new, ignore_errors=True)


def bench_json(n):
    return json.dumps([{"n_prompt": 512, "n_gen": 0, "n_depth": 0, "avg_ts": 8.4 + i, "stddev_ts": 0.1}
                       for i in range(n)])


class Validation(unittest.TestCase):
    def test_a_good_bench_spec_has_no_defects(self):
        self.assertEqual(tf.spec_defects(GOOD), [])

    def test_defects_are_named(self):
        def bad(**kw):
            return dict(GOOD, runs=[dict(GOOD["runs"][0], **kw)])
        for spec in (bad(where="mordor"), bad(model="/etc/passwd"), bad(p=[0], n=[0]), bad(r=0),
                     bad(p="512"), dict(GOOD, runs=[]),
                     dict(GOOD, runs=[GOOD["runs"][0], dict(GOOD["runs"][0])]),             # duplicate id
                     dict(GOOD, runs=[dict(GOOD["runs"][2], gpu_layers=8)]),               # GPU on a tablet
                     dict(GOOD, runs=[dict(GOOD["runs"][3], where="rigby")]),              # link on rigby
                     dict(GOOD, runs=[dict(GOOD["runs"][3], link_mb=4096)]),
                     dict(GOOD, runs=[dict(GOOD["runs"][3], model="m.gguf")]),
                     dict(GOOD, runs=[dict(GOOD["runs"][1], gpu_layers=None)])):           # op_offload alone
            self.assertTrue(tf.spec_defects(spec), spec)

    def test_lanes(self):
        self.assertEqual(tv.lane_of_spec(GOOD), "fleet")
        self.assertEqual(tv.lane_of_spec(dict(GOOD, runs=GOOD["runs"][:2])), "rigby")
        self.assertEqual(tv.bench_tablets(GOOD), ["gandalf"])


class Argv(unittest.TestCase):
    def test_rigby_cpu_run(self):
        a = tv.bench_argv(GOOD["runs"][0], "/models/m.gguf")
        self.assertEqual(os.path.basename(a[0]), "llama-bench")
        self.assertEqual(a[a.index("-o") + 1], "jsonl")
        self.assertEqual(a[a.index("-p") + 1], "1,2,4,8,512")
        self.assertEqual(a[a.index("-n") + 1], "64")
        self.assertEqual(a[a.index("-d") + 1], "0,2048")
        self.assertNotIn("-ngl", a)

    def test_rigby_card_run_uses_the_host_loader_and_the_vulkan_build(self):
        a = tv.bench_argv(dict(GOOD["runs"][1], override_tensor=["ffn_=CPU", "output\\.=CPU"]), "/models/m.gguf")
        self.assertTrue(a[0].endswith("ld-linux-x86-64.so.2"))
        self.assertTrue(a[3].startswith(tv.LLAMA_VULKAN))
        self.assertEqual(a[a.index("-ngl") + 1], "8")
        self.assertEqual(a[a.index("-ot") + 1], "ffn_=CPU;output\\.=CPU")
        self.assertEqual(a[a.index("-nopo") + 1], "1")
        self.assertEqual(a[a.index("-n") + 1], "0", "no writing test asked for")

    def test_a_placement_names_its_card_and_only_the_730_gets_the_kepler_flags(self):
        run = dict(GOOD["runs"][1], gpu="gtx1060", build="next")
        a = tv.bench_argv(run, "/models/m.gguf", tv.pick_vulkan(VK_TWO, "gtx1060"))
        self.assertEqual((a[a.index("-dev") + 1], a[a.index("-sm") + 1]), ("Vulkan0", "none"))
        self.assertNotIn("GGML_VK_DISABLE_F16", tv.bench_head(run)[1])
        self.assertEqual(tv.bench_head(dict(run, gpu="gt730"))[1].get("GGML_VK_DISABLE_F16"), "1")
        self.assertEqual(tv.pick_vulkan(VK_TWO, "gt730"), "Vulkan1")
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        for bad in (dict(run, gpu="rtx4090"), dict(GOOD["runs"][0], gpu="gtx1060")):     # unknown card; no placement
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)

    def test_fit_target_lets_llamas_fitter_place_the_model_on_the_named_card(self):
        # round 34: a hand -ngl 99 + expert -ot asked the 1060 for weights + a 2 GB compute buffer and OOMed;
        # llama.cpp's fitter sizes both against free memory, but only for arguments left unset
        run = {"id": "f", "where": "rigby", "model": "m.gguf", "build": "next", "p": [4096], "n": [128],
               "d": [0, 16384], "gpu": "gtx1060", "fit_target": 1024}
        a = tv.bench_argv(run, "/models/m.gguf", tv.pick_vulkan(VK_TWO, "gtx1060"))
        self.assertEqual((a[a.index("-dev") + 1], a[a.index("-fitt") + 1], a[a.index("-fitc") + 1]),
                         ("Vulkan0", "1024", str(tv.bench_n_ctx(run))))
        self.assertNotIn("-ngl", a)
        self.assertNotIn("-ot", a)
        self.assertTrue(tv.uses_card(run))
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        for bad in (dict(run, gpu_layers=99), dict(run, fit_target=-1), dict(run, fit_target=True)):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)

    def test_kv_cache_type_is_a_rigby_bench_knob(self):
        run = dict(GOOD["runs"][1], cache_type_k="q8_0", cache_type_v="q8_0")
        a = tv.bench_argv(run, "/models/m.gguf")
        self.assertEqual((a[a.index("-ctk") + 1], a[a.index("-ctv") + 1]), ("q8_0", "q8_0"))
        self.assertNotIn("-ctk", tv.bench_argv(GOOD["runs"][1], "/models/m.gguf"))
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        for bad in (dict(run, cache_type_k="q3_k"), dict(GOOD["runs"][2], cache_type_k="q8_0")):   # tablet run
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)

    def test_tablet_command_uses_every_core_and_keeps_stdout_clean(self):
        c = tv.tablet_bench_cmd(GOOD["runs"][2])
        self.assertIn("nproc", c)
        self.assertIn("-o json", c)
        self.assertIn("2>/dev/null", c)
        self.assertIn("-t 2", tv.tablet_bench_cmd(dict(GOOD["runs"][2], threads=2)))

    def test_a_tablet_run_carries_its_chunk_size(self):
        run = dict(GOOD["runs"][2], ub=64)
        self.assertIn(" -ub 64", tv.tablet_bench_cmd(run))
        self.assertNotIn("-ub", tv.tablet_bench_cmd(GOOD["runs"][2]))

    def test_a_tablet_run_cannot_ask_for_a_build_it_would_not_get(self):
        self.assertTrue(tf.spec_defects(dict(GOOD, runs=[dict(GOOD["runs"][2], build="next")])))
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[dict(GOOD["runs"][0], build="next")])), [])

    def test_expected_result_count(self):
        self.assertEqual(tv.bench_expected(GOOD["runs"][0]), 12)          # (5 prompts + 1 write) x 2 depths
        self.assertEqual(tv.bench_expected(GOOD["runs"][1]), 1)


class Parse(unittest.TestCase):
    def test_json_is_found_inside_noise(self):
        self.assertEqual(len(tv.parse_bench("load...\n" + bench_json(2) + "\nbye", 0)), 2)

    def test_a_failed_or_empty_run_raises(self):
        for out, rc in (("", 0), (bench_json(1), 1), ("[]x", 0), ('[{"avg_ts": 0}]', 0)):
            with self.assertRaises((RuntimeError, ValueError)):
                tv.parse_bench(out, rc)


class RunBench(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.prog = []
        self.patches = [
            mock.patch.object(tv, "run_stoppable",
                              side_effect=lambda argv, env, stop=None, beat=None, on_line=None: (0, bench_json(
                                  tv.bench_expected(self._run_for(argv))), "")),
            mock.patch.object(tv, "stage_tablet_bench"),
            mock.patch.object(tf, "_ssh", side_effect=lambda h, cmd, timeout=None: (0, bench_json(1))),
            mock.patch.object(tv, "link_throughput",
                              side_effect=lambda h, mb: {"route": "wire", "mb": mb, "to_tablet_mb_s": 30.0}),
            mock.patch.object(tv, "vulkan_device", side_effect=lambda vk_dir, gpu: tv.pick_vulkan(VK_TWO, gpu)),
        ]
        for p in self.patches:
            p.start()
        self._clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._clear()

    def _clear(self):
        for f in os.listdir(tf.STATE_DIR):
            os.remove(os.path.join(tf.STATE_DIR, f))

    def _ckpt(self):
        with open(os.path.join(tf.STATE_DIR, "b1.checkpoint.jsonl"), encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def _run_for(self, argv):
        if "-ngl" in argv:
            return GOOD["runs"][1]
        return GOOD["runs"][0]

    def go(self, spec=GOOD, stop=None):
        return tv.run_bench(spec, self.logs.append, lambda i, n, rid: self.prog.append((i, n, rid)), stop)

    def _staged_with_dram(self, readings):
        spec = dict(GOOD, runs=[dict(STAGED, p=[512])])
        calls = []
        with mock.patch.object(tf, "dram_health", side_effect=lambda h: readings.get(h)), \
                mock.patch.object(tv, "run_stages", side_effect=lambda *a, **k: calls.append(a[0]) or (
                    json.loads(bench_json(1)), {"stages": a[0]["stages"]})):
            rows, errors = self.go(spec)
        return rows, errors, calls

    def test_a_degraded_tablet_fails_its_stage_run_before_it_starts(self):
        rows, errors, calls = self._staged_with_dram({"gandalf": 2.9, "frodo": 0.7})
        self.assertEqual(calls, [], "nothing loaded onto a tablet in a degraded memory state")
        self.assertIn("frodo", errors["pipe"])
        self.assertIn("reboot", errors["pipe"])

    def test_an_unmeasurable_tablet_is_never_read_as_healthy(self):
        rows, errors, calls = self._staged_with_dram({"gandalf": 2.9, "frodo": None})
        self.assertEqual(calls, [])
        self.assertIn("pipe", errors)

    def test_healthy_dram_readings_ride_on_the_rows(self):
        rows, errors, calls = self._staged_with_dram({"gandalf": 2.9, "frodo": 4.67})
        self.assertEqual(errors, {})
        self.assertEqual(rows[0]["dram_mgather"], {"gandalf": 2.9, "frodo": 4.67})

    def test_every_run_lands_its_rows(self):
        rows, errors = self.go()
        self.assertEqual(errors, {})
        self.assertEqual([r["run"] for r in rows].count("rigby-7b"), 12)
        self.assertEqual({r["test"] for r in rows if r["run"] == "gandalf-link"}, {"link"})
        self.assertEqual(self.prog[-1], (4, 4, "gandalf-link"))

    def test_a_short_result_count_is_an_error_with_no_rows(self):
        tv.run_stoppable.side_effect = lambda argv, env, stop=None, beat=None, on_line=None: (0, bench_json(3), "")
        rows, errors = self.go(dict(GOOD, runs=GOOD["runs"][:1]))
        self.assertEqual(rows, [])
        self.assertIn("expected 12", errors["rigby-7b"])

    def test_a_stop_mid_run_ends_the_order_and_records_nothing_for_that_run(self):
        tv.run_stoppable.side_effect = tv.BenchStopped("stopped by a slip mid-run")
        rows, errors = self.go()
        self.assertEqual((rows, errors), ([], {}))
        self.assertTrue(any("STOPPED mid-run" in m for m in self.logs))
        self.assertEqual(self.prog, [], "nothing after the stopped run is dealt")


    def test_every_finished_run_is_checkpointed_as_it_lands(self):
        rows, _ = self.go()
        self.assertEqual(len(self._ckpt()), len(rows))

    def test_a_requeue_keeps_complete_runs_and_reruns_the_rest(self):
        self.go()
        ck = [r for r in self._ckpt() if r["run"] != "gandalf-link"]            # the link run never finished
        with open(os.path.join(tf.STATE_DIR, "b1.checkpoint.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in ck[:-1])                  # and the tablet run is one short
        calls = []
        tv.link_throughput.side_effect = lambda h, mb: calls.append(h) or {"route": "wire", "mb": mb}
        rows, errors = self.go()
        self.assertEqual(errors, {})
        self.assertTrue(all(r.get("resumed") for r in rows if r["run"].startswith("rigby")))
        self.assertFalse(any(r.get("resumed") for r in rows if r["run"] in ("gandalf-1.5b", "gandalf-link")))
        self.assertEqual(calls, ["gandalf"])

    def test_a_stop_ends_between_runs(self):
        ev = threading.Event()
        ev.set()
        rows, errors = self.go(stop=ev)
        self.assertEqual((rows, self.prog), ([], []))


STAGED = {"id": "pipe", "where": "rigby", "model": "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "p": [2048], "ub": 256,
          "build": "next", "r": 2,
          "stages": [{"dev": "card", "ts": 9}, {"dev": "rigby", "ts": 9}, {"dev": "gandalf", "ts": 2},
                     {"dev": "frodo", "ts": 2}]}
# rigby's `llama-server --list-devices` Vulkan lines, verbatim
VK_TWO = [("Vulkan0", "NVIDIA GeForce GTX 1060 6GB (6390 MiB, 6308 MiB free)"),
          ("Vulkan1", "NVIDIA GeForce GT 730 (2048 MiB, 2000 MiB free)")]


class Stages(unittest.TestCase):
    """#36 pipelined reading: every layer on a stage (rigby's CPU over localhost RPC, the card, the tablets)."""

    def test_a_staged_run_validates_and_takes_the_fleet_lane(self):
        spec = dict(GOOD, runs=[STAGED])
        self.assertEqual(tf.spec_defects(spec), [])
        self.assertEqual(tv.bench_tablets(spec), ["gandalf", "frodo"])
        self.assertEqual(tv.lane_of_spec(spec), "fleet")

    def test_stage_defects_are_named(self):
        def bad(**kw):
            return dict(GOOD, runs=[dict(STAGED, **kw)])
        for spec in (bad(build=None), bad(where="gandalf"), bad(stages=[{"dev": "rigby", "ts": 1}]),
                     bad(stages=[{"dev": "rigby", "ts": 1}, {"dev": "mordor", "ts": 1}]),
                     bad(stages=[{"dev": "rigby", "ts": 1}, {"dev": "rigby", "ts": 1}]),
                     bad(stages=[{"dev": "rigby", "ts": 0}, {"dev": "card", "ts": 1}]),
                     bad(gpu_layers=8), bad(ub=0), bad(build="nightly")):
            self.assertTrue(tf.spec_defects(spec), spec)

    def test_devices_map_by_endpoint_or_by_order_and_refuse_a_miscount(self):
        eps = {"rigby": "127.0.0.1:50061", "gandalf": "192.0.2.16:50062", "frodo": "192.0.2.11:50062"}
        st = STAGED["stages"]
        named = VK_TWO + ["RPC0[127.0.0.1:50061]", "RPC1[192.0.2.11:50062]", "RPC2[192.0.2.16:50062]"]
        self.assertEqual(tv.map_stage_devices(st, named, eps),
                         {"card": "Vulkan1", "rigby": "RPC0[127.0.0.1:50061]", "gandalf": "RPC2[192.0.2.16:50062]",
                          "frodo": "RPC1[192.0.2.11:50062]"}, "the card is the GT 730 by NAME, not the first Vulkan")
        self.assertEqual(tv.map_stage_devices(st, VK_TWO + ["RPC0", "RPC1", "RPC2"], eps)["gandalf"], "RPC1")
        for names in (VK_TWO + ["RPC0", "RPC1"], ["RPC0", "RPC1", "RPC2"], VK_TWO[:1] + ["RPC0", "RPC1", "RPC2"]):
            with self.assertRaises(RuntimeError):
                tv.map_stage_devices(st, names, eps)

    def test_device_lines_are_parsed_in_both_spellings(self):
        out = ("Available devices:\n  Vulkan0: NVIDIA GeForce GT 730 (2048 MiB, 1998 MiB free)\n"
               "  RPC0: 127.0.0.1:50071 (23976 MiB, 23976 MiB free)\n"
               "  RPC1[192.0.2.16:50062]: RPC (2700 MiB, 2600 MiB free)\n")
        got = tv._DEV_LINE.findall(out)
        self.assertEqual([n for n, _ in got], ["Vulkan0", "RPC0", "RPC1[192.0.2.16:50062]"])
        eps = {"rigby": "127.0.0.1:50071", "gandalf": "192.0.2.16:50062"}
        st = [{"dev": "gandalf", "ts": 1}, {"dev": "rigby", "ts": 1}]      # -rpc order differs from list order
        self.assertEqual(tv.map_stage_devices(st, got, eps), {"gandalf": "RPC1[192.0.2.16:50062]", "rigby": "RPC0"})

    def test_rpc_comes_before_list_devices(self):
        with mock.patch.object(tv.subprocess, "run", return_value=mock.Mock(stdout="", stderr="")) as run:
            tv.bench_devices(["llama-bench"], {}, ["127.0.0.1:50061"])
        argv = run.call_args[0][0]
        self.assertLess(argv.index("-rpc"), argv.index("--list-devices"))

    def test_the_stage_command_meets_llamacpps_overlap_conditions(self):
        eps = {"rigby": "127.0.0.1:50061", "gandalf": "192.0.2.16:50062", "frodo": "192.0.2.11:50062"}
        dm = {"card": "Vulkan0", "rigby": "RPC0", "gandalf": "RPC1", "frodo": "RPC2"}
        head, env = tv.bench_head(STAGED)
        a = tv.stage_argv(STAGED, head, "/models/m.gguf", eps, dm)
        self.assertTrue(a[3].startswith(tv.next_dirs()["vulkan"]), "the card needs the next Vulkan build")
        self.assertEqual(a[a.index("-ngl") + 1], "99")
        self.assertEqual(a[a.index("-sm") + 1], "layer")
        self.assertEqual(a[a.index("-rpc") + 1], "127.0.0.1:50061,192.0.2.16:50062,192.0.2.11:50062")
        self.assertEqual(a[a.index("-dev") + 1], "Vulkan0/RPC0/RPC1/RPC2")
        self.assertEqual(a[a.index("-ts") + 1], "9/9/2/2")
        self.assertEqual(a[a.index("-ub") + 1], "256")
        self.assertNotIn("-ot", a, "any tensor override turns pipeline parallelism off")
        self.assertEqual(env.get("GGML_VK_DISABLE_F16"), "1")

    def test_the_next_build_cpu_run_uses_its_own_dir(self):
        head, env = tv.bench_head({"build": "next"})
        self.assertTrue(head[0].startswith(tv.next_dirs()["bin"]))
        self.assertEqual(env["LD_LIBRARY_PATH"], tv.next_dirs()["bin"])

    def test_a_stage_run_refuses_a_low_or_unreadable_battery_before_loading_anything(self):
        started = []
        res, _killed, _local = self._stage_run(battery=9, started=started)
        self.assertIsInstance(res, RuntimeError)
        self.assertIn("battery 9%", str(res))
        self.assertIn("nothing loaded", str(res))
        self.assertEqual(started, [], "no stage server started on a refused run")
        res, _killed, _local = self._stage_run(battery=None, started=[])
        self.assertIsInstance(res, RuntimeError)
        self.assertIn("could not read its battery", str(res))

    def _stage_run(self, fail_at=None, run=STAGED, err=None, seen_env=None, need=100.0, have=2000, started=None,
                   seen_since=None, kv=0.0, popen_calls=None, battery=80):
        killed, local = [], mock.Mock()
        local.poll.return_value = None
        pp_log = "llama_context: pipeline parallelism enabled\n" + (err or "")
        tabs = [s["dev"] for s in run["stages"] if s["dev"] not in tv.STAGE_DEVS]

        def bench(argv, env, stop_event=None, beat=None, on_line=None):
            if seen_env is not None:
                seen_env.update(env)
            return 0, bench_json(1), pp_log
        patches = [
            mock.patch.object(tv.subprocess, "Popen",
                              side_effect=lambda argv, **kw: (popen_calls.append(argv)
                                                              if popen_calls is not None else None) or local),
            mock.patch.object(tv, "port_open", return_value=True),
            mock.patch.object(tv, "rpc_server_name", return_value="rpc-server"),
            mock.patch.object(tv, "kill_backends", side_effect=lambda h, adb, port=0: killed.append(list(h)) or []),
            mock.patch.object(tv, "start_backends",
                              side_effect=lambda h, log, **kw: (started.append(kw) if started is not None else None)
                              or list(h)),
            mock.patch.object(tv, "stage_weight_mib",
                              return_value={s["dev"]: need for s in run["stages"]}),
            mock.patch.object(tv, "stage_kv_mib",
                              return_value={s["dev"]: kv for s in run["stages"]}),
            mock.patch.object(tv, "unit_mix", return_value=(sum(s["ts"] for s in run["stages"]) - 1,
                                                            [{}] * sum(s["ts"] for s in run["stages"]))),
            mock.patch.object(tv, "tablet_mem_avail",
                              side_effect=lambda nodes, since=None: (seen_since.append(since)
                                                                     if seen_since is not None else None)
                              or {t: have for t in tabs}),
            mock.patch.object(tv, "tablet_battery",
                              side_effect=lambda nodes: {} if battery is None else {t: (battery, "Discharging")
                                                                                     for t in tabs}),
            mock.patch.object(tv, "bench_devices", side_effect=RuntimeError("list failed") if fail_at == "list"
                              else (lambda head, env, eps: (VK_TWO + ["RPC0", "RPC1", "RPC2"], ""))),
            mock.patch.object(tv, "run_stoppable", side_effect=bench),
        ]
        for p in patches:
            p.start()
        try:
            return tv.run_stages(run, lambda m: None, lambda *a, **k: "", order_name="b1"), killed, local
        except RuntimeError as e:
            return e, killed, local
        finally:
            for p in patches:
                p.stop()

    def test_a_stage_run_records_overlap_and_cleans_up(self):
        (res, meta), killed, local = self._stage_run()
        self.assertTrue(meta["pipeline_parallel"])
        self.assertIn("max_busy_stages", meta, "the second overlap instrument is recorded on every stage run")
        self.assertEqual(meta["devices"], ["Vulkan1", "RPC0", "RPC1", "RPC2"])
        self.assertEqual(killed, [["gandalf", "frodo"], ["gandalf", "frodo"]])   # orphans first, then cleanup
        local.terminate.assert_called_once()

    def test_a_failed_stage_run_still_stops_every_server(self):
        err, killed, local = self._stage_run(fail_at="list")
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(len(killed), 2)
        local.terminate.assert_called_once()

    def test_a_traced_run_ships_its_log_and_names_each_stage(self):
        import shutil
        import tempfile
        out = tempfile.mkdtemp(prefix="bench_outbox_")
        self.addCleanup(shutil.rmtree, out, True)
        # the real two-server trace, its endpoints renamed to this run's rigby and gandalf stages
        text = real_trace().replace("127.0.0.1:50091", "127.0.0.1:50061").replace("127.0.0.1:50092",
                                                                                   "192.0.2.16:50062")
        env = {}
        with mock.patch.object(tf, "OUTBOX", out):
            (res, meta), _k, _l = self._stage_run(run=dict(STAGED, trace=True), err=text, seen_env=env)
        self.assertEqual(env.get("GGML_RPC_TRACE"), "1")
        self.assertEqual(set(meta["trace"]), {"rigby", "gandalf"}, "keyed by stage; the card has no RPC trace")
        self.assertEqual(meta["trace"]["gandalf"]["computes"], 16)
        with open(meta["trace_log"], encoding="utf-8") as f:
            self.assertIn("RPCTRACE", f.read())
        self.assertEqual(os.path.dirname(meta["trace_log"]), os.path.join(out, "trace", "b1"))

    def test_an_untraced_run_does_not_turn_the_trace_on(self):
        env = {}
        (res, meta), _k, _l = self._stage_run(seen_env=env)
        self.assertNotIn("GGML_RPC_TRACE", env)
        self.assertNotIn("trace", meta)

    def test_a_trace_asked_for_but_absent_fails_the_run_loudly(self):
        import shutil
        import tempfile
        out = tempfile.mkdtemp(prefix="bench_outbox_")
        self.addCleanup(shutil.rmtree, out, True)
        with mock.patch.object(tf, "OUTBOX", out):           # never the live outbox
            err, _k, local = self._stage_run(run=dict(STAGED, trace=True), err="")
        self.assertIsInstance(err, RuntimeError, "a build without the trace patch must not read as 'no RPC work'")
        local.terminate.assert_called_once()

    def test_the_fit_check_reads_memory_measured_after_the_old_servers_died(self):
        import time as _t
        seen, t0 = [], _t.time()
        self._stage_run(seen_since=seen)
        self.assertTrue(seen and all(s is not None and s >= t0 for s in seen),
                        "round 13: refused on samples taken while the previous run's servers held their weights")

    def test_tablet_servers_start_with_the_weight_cache(self):
        started = []
        self._stage_run(started=started)
        self.assertTrue(started and started[0].get("cache") is True)

    def test_a_stage_that_will_not_fit_is_refused_before_anything_starts(self):
        started = []
        err, killed, local = self._stage_run(need=900.0, have=1071, started=started)
        self.assertIsInstance(err, RuntimeError)
        self.assertIn("will not fit", str(err))
        self.assertEqual(started, [], "no server started, nothing loaded")

    def test_rigby_can_hold_several_stages_each_its_own_server_sharing_its_cores(self):
        run = dict(STAGED, stages=[{"dev": "rigby2", "ts": 3}, {"dev": "gandalf", "ts": 2}, {"dev": "rigby", "ts": 9}])
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        calls = []
        (res, meta), killed, local = self._stage_run(run=dict(run, threads=12), popen_calls=calls)
        ports = sorted(a[a.index("-p") + 1] for a in calls)
        self.assertEqual(ports, sorted([str(tv.RIGBY_RPC_PORT), str(tv.rigby_port("rigby2"))]))
        self.assertEqual(meta["endpoints"]["rigby2"], "127.0.0.1:%d" % tv.rigby_port("rigby2"))
        self.assertEqual(meta["rigby_threads"], {"rigby2": 3, "rigby": 9}, "12 cores in proportion to 3:9 layers")
        self.assertEqual(killed, [["gandalf"], ["gandalf"]], "rigby's stages are not tablets")
        self.assertEqual(local.terminate.call_count, 2, "every local server is stopped")

    def test_several_rigby_servers_keep_their_output_beside_the_trace(self):
        import shutil
        import tempfile
        out = tempfile.mkdtemp(prefix="bench_outbox_")
        self.addCleanup(shutil.rmtree, out, True)
        run = dict(STAGED, stages=[{"dev": "rigby2", "ts": 3}, {"dev": "gandalf", "ts": 2}, {"dev": "rigby", "ts": 9}])
        with mock.patch.object(tf, "OUTBOX", out):
            self._stage_run(run=run)
        logs = sorted(os.listdir(os.path.join(out, "trace", "b1")))
        self.assertEqual(logs, ["pipe.rigby.rpc.log", "pipe.rigby2.rpc.log"])

    def test_a_last_stage_holding_only_the_output_is_refused_before_anything_starts(self):
        started, calls = [], []
        run = dict(STAGED, stages=[{"dev": "gandalf", "ts": 6}, {"dev": "rigby2", "ts": 6}, {"dev": "rigby", "ts": 1}])
        err, killed, local = self._stage_run(run=run, started=started, popen_calls=calls)
        self.assertIsInstance(err, RuntimeError)
        self.assertIn("only the output layer", str(err))
        self.assertEqual((started, calls), ([], []), "rounds 13 and 19b-21: refused, nothing loaded")

    def test_handoff_reads_on_the_split_and_writes_on_rigby_alone(self):
        order, calls, local = [], [], [mock.Mock(name="rigby-stage-server")]
        local[0].terminate.side_effect = lambda: order.append("rigby stages stopped")

        def serve(argv, env, port, log_file, stop_event):
            if log_file:
                log_file.close()
            order.append("reader up" if "--rpc" in argv else "writer up")
            p = mock.Mock()
            p.terminate.side_effect = lambda: order.append("stopped :%d" % port)
            return p

        def http(port, path, body=None, timeout=None):
            calls.append((port, path, body))
            if path == "/tokenize":
                return {"tokens": [7, 8, 9] if "committee" in body["content"] else [42]}
            if path.startswith("/slots"):
                return {"n_written": 80 << 20, "n_restored": 4096, "timings": {"save_ms": 900, "restore_ms": 700}}
            if path == "/health":
                return {}
            t = {"prompt_ms": 1.0, "prompt_n": 1 if port == tv.HANDOFF_PORTS[1] else 10, "prompt_per_second": 1.0,
                 "predicted_per_second": 5.0, "predicted_n": 4}
            return {"timings": t, "content": "same words"}
        run = dict(STAGED, p=[10], n=[4], handoff=True, stages=[{"dev": "gandalf", "ts": 2}, {"dev": "rigby", "ts": 2}])
        eps = {"gandalf": "192.0.2.16:50062", "rigby": "127.0.0.1:50061"}
        with mock.patch.object(tv, "_serve", side_effect=serve), mock.patch.object(tv, "_http_json", side_effect=http):
            res, m = tv.handoff_body(run, "/b", {}, eps, {"gandalf": "RPC0", "rigby": "RPC1"}, local, None, "b1")
        self.assertLess(order.index("rigby stages stopped"), order.index("writer up"), "free rigby before the writer")
        prompts = [b["prompt"] for p, path, b in calls if path == "/completion"]
        self.assertEqual(prompts[0], [7, 8, 9, 7, 8, 9, 7, 8, 9, 7])
        self.assertEqual(prompts[1], prompts[0] + [42], "the writer's prompt extends the read one by a token")
        self.assertEqual((m["writer_reread_tokens"], m["identical"], res[0]["avg_ts"]), (1, True, 5.0))
        self.assertEqual(tv.bench_expected(run), 1)
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[dict(run, build="next")])), [])
        for bad in (dict(run, n=[4, 8]), dict(run, handoff="yes"), dict(run, d=[0])):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)

    def test_handoff_writer_can_be_rigby_plus_the_card(self):
        # A1 writes on rigby + the GT 730 (attention + KV on the card); the card wins past ~8K of context
        argvs = {}

        def serve(argv, env, port, log_file, stop_event):
            if log_file:
                log_file.close()
            argvs[port] = (argv, env)
            return mock.Mock()

        def http(port, path, body=None, timeout=None):
            if path == "/tokenize":
                return {"tokens": [7]}
            if path.startswith("/slots"):
                return {"n_written": 1, "n_restored": 10, "timings": {}}
            t = {"prompt_ms": 1.0, "prompt_n": 1, "prompt_per_second": 1.0, "predicted_per_second": 3.0,
                 "predicted_n": 4}
            return {"timings": t, "content": "x"}
        ot = ["blk\\.(?!(3|7|11|15|19|23|27|31|35|39)\\.).*=CPU", "ffn_=CPU", "output\\.=CPU"]
        card = {"gpu_layers": 99, "override_tensor": ot, "op_offload": False}
        run = dict(STAGED, p=[10], n=[4], handoff=True, build="next", writer=card,
                   stages=[{"dev": "gandalf", "ts": 2}, {"dev": "rigby", "ts": 2}])
        eps = {"gandalf": "192.0.2.16:50062", "rigby": "127.0.0.1:50061"}
        with mock.patch.object(tv, "_serve", side_effect=serve), mock.patch.object(tv, "_http_json", side_effect=http), \
                mock.patch.object(tv, "vulkan_device", side_effect=lambda vk_dir, gpu: tv.pick_vulkan(VK_TWO, gpu)):
            tv.handoff_body(run, "/b", {}, eps, {"gandalf": "RPC0", "rigby": "RPC1"}, [], None, "b1")
        w, wenv = argvs[tv.HANDOFF_PORTS[1]]
        self.assertEqual((w[w.index("-dev") + 1], w[w.index("-sm") + 1]), ("Vulkan1", "none"),
                         "pinned to the GT 730 by name - with two cards an unpinned writer is split across both")
        self.assertEqual(wenv.get("GGML_VK_DISABLE_F16"), "1", "Kepler flags for the 730")
        r, _ = argvs[tv.HANDOFF_PORTS[0]]
        self.assertTrue(w[0].endswith("ld-linux-x86-64.so.2"))
        self.assertEqual(w[3], os.path.join(tv.next_dirs()["vulkan"], "llama-server"))
        self.assertEqual(w[w.index("-ngl") + 1], "99")
        self.assertEqual([w[i + 1] for i, a in enumerate(w) if a == "-ot"], ot)
        self.assertIn("--no-op-offload", w)
        self.assertNotIn("LD_LIBRARY_PATH", wenv)
        self.assertNotIn("--no-op-offload", r, "the reader is unchanged")
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        plain = {k: v for k, v in run.items() if k not in ("stages", "handoff")}
        for bad in (dict(run, handoff=None), dict(run, writer=dict(card, threads=2)),
                    dict(run, writer={"override_tensor": ot}), dict(run, writer=dict(card, op_offload="no")), plain):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)

    def test_kld_parses_llama_perplexitys_report_and_refuses_a_silent_one(self):
        report = ("Final estimate: PPL = 7.1234 +/- 0.05\n====== Perplexity statistics ======\n"
                  "Mean PPL(Q)                   :   7.130000 ±   0.050000\nMean PPL(base)                :   "
                  "7.123400 ±   0.050000\n====== KL divergence statistics ======\nMean    KLD:   0.004321 ±   0.0001\n"
                  "Maximum KLD:   1.234000\n99.0%   KLD:   0.051000\n====== Token probability statistics ======\n"
                  "Same top p: 97.123 ± 0.150 %\n")
        res, m = tv.parse_kld(report, 0, True)
        self.assertEqual((m["kld_mean"], m["same_top_pct"], m["kld_p99"], m["ppl_q"]), (0.004321, 97.123, 0.051, 7.13))
        self.assertEqual(len(res), 1)
        self.assertEqual(tv.parse_kld("Final estimate: PPL = 7.1\n", 0, False)[1]["ppl"], 7.1)
        with self.assertRaises(RuntimeError):
            tv.parse_kld("Final estimate: PPL = 7.1\n", 0, True)
        with self.assertRaises(RuntimeError):
            tv.parse_kld(report, 1, True)

    def test_kld_runs_are_checked_and_score_against_their_orders_base(self):
        base = {"id": "base", "where": "rigby", "build": "next", "model": "m.gguf", "kld_base": True,
                "kld_text": "t.txt", "chunks": 8}
        score = dict(STAGED, id="split", kld=True, kld_text="t.txt", chunks=8)
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[base, score])), [])
        for bad in (dict(base, kld=True), dict(base, chunks=0), dict(base, kld_text="../x"),
                    dict(base, stages=STAGED["stages"]), dict(base, where="gandalf", build=None)):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)
        argv = tv.kld_argv(score, ["/b/llama-bench"], "/m/m.gguf", tv.kld_base_path("o1", score))
        self.assertEqual(argv[0], os.path.join("/b", "llama-perplexity"))
        self.assertIn("--kl-divergence", argv)
        self.assertEqual(argv[argv.index("--kl-divergence-base") + 1], tv.kld_base_path("o1", base))
        self.assertNotIn("--kl-divergence", tv.kld_argv(base, ["/b/llama-bench"], "/m/m.gguf", "/x.kld"))
        self.assertEqual(tv.bench_expected(score), 1)

    def test_an_imatrix_run_counts_routing_on_the_named_card_and_ships_its_file(self):
        run = {"id": "route", "where": "rigby", "build": "cuda", "model": "m.gguf", "gpu": "gtx1060",
               "fit_target": 1024, "imatrix": True, "imatrix_text": "a1.txt", "chunks": 120}
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[run])), [])
        for bad in (dict(run, imatrix_text="../a1.txt"), dict(run, chunks=0), dict(run, imatrix="yes"),
                    dict(run, kld=True, kld_text="t.txt"), dict(run, stages=STAGED["stages"]),
                    {k: v for k, v in run.items() if k != "imatrix_text"}):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[bad])), bad)
        out = tv.imatrix_out_path("o1", run)
        self.assertTrue(out.endswith(os.path.join("outbox", "o1.route.imatrix.gguf")), out)
        a = tv.imatrix_argv(run, ["/b/llama-bench"], "/m/m.gguf", out, "CUDA0")
        self.assertEqual(a[0], os.path.join("/b", "llama-imatrix"))
        self.assertEqual(a[a.index("-f") + 1], tv.imatrix_text_path("a1.txt"))
        self.assertEqual(a[a.index("-o") + 1], out)
        for flag in ("--no-ppl", "--parse-special"):
            self.assertIn(flag, a)
        self.assertEqual((a[a.index("-dev") + 1], a[a.index("-fitt") + 1]), ("CUDA0", "1024"))
        self.assertEqual(tv.bench_expected(run), 1)

    def test_an_imatrix_run_that_wrote_nothing_is_an_error_not_a_count(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "x.imatrix.gguf")
        with self.assertRaises(RuntimeError):
            tv.parse_imatrix("compute_imatrix: computing over 120 chunks", 0, p)       # exit 0, no file
        with self.assertRaises(RuntimeError):
            tv.parse_imatrix("", 1, p)
        with open(p, "wb") as fh:
            fh.write(b"GGUF....")
        rows, meta = tv.parse_imatrix("compute_imatrix: computing over 120 chunks", 0, p)
        self.assertEqual((len(rows), meta["chunks_computed"], meta["bytes"]), (1, 120, 8))

    def test_rigby_stage_names_and_threads_are_checked(self):
        for st in ([{"dev": "rigby17", "ts": 1}, {"dev": "rigby", "ts": 1}],
                   [{"dev": "rigby2", "ts": 1}, {"dev": "gandalf", "ts": 1, "threads": 4}],
                   [{"dev": "rigby2", "ts": 1, "threads": 0}, {"dev": "rigby", "ts": 1}]):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[dict(STAGED, stages=st)])), st)

    def test_the_fit_check_counts_the_kv_cache_a_long_prompt_adds(self):
        started = []
        (res, meta), _k, _l = self._stage_run(need=372.0, kv=0.0, have=1071)
        self.assertEqual(meta["stage_mem_mib"]["gandalf"], [372, 0, 1071])
        err, _k, _l = self._stage_run(need=372.0, kv=512.0, have=1071, started=started)
        self.assertIsInstance(err, RuntimeError, "372 MiB fits at 4K; the same layer's 256K cache does not")
        self.assertIn("KV cache", str(err))
        self.assertEqual(started, [])

    def test_graph_reuse_false_turns_llamas_reuse_drain_off(self):
        env = {}
        (res, meta), _k, _l = self._stage_run(run=dict(STAGED, graph_reuse=False), seen_env=env)
        self.assertEqual(env.get("LLAMA_GRAPH_REUSE_DISABLE"), "1")

    def test_graph_reuse_is_llamas_default_unless_turned_off(self):
        for run in (STAGED, dict(STAGED, graph_reuse=True)):
            env = {}
            self._stage_run(run=run, seen_env=env)
            self.assertNotIn("LLAMA_GRAPH_REUSE_DISABLE", env)

    def test_graph_reuse_needs_stages_and_a_bool(self):
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[dict(STAGED, graph_reuse=False)])), [])
        for run in (dict(STAGED, graph_reuse=0), dict(GOOD["runs"][0], graph_reuse=False)):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[run])), run)

    def test_trace_needs_stages_and_a_bool(self):
        self.assertEqual(tf.spec_defects(dict(GOOD, runs=[dict(STAGED, trace=True)])), [])
        for run in (dict(STAGED, trace="yes"), dict(GOOD["runs"][0], trace=True)):
            self.assertTrue(tf.spec_defects(dict(GOOD, runs=[run])), run)


def write_gguf(path, tensors, block_count, arch="qwen2", align=32, tail=0):
    """A minimal real-format GGUF (v3): metadata (architecture, block_count, alignment) + tensor infos + aligned
    data. tensors = [(name, nbytes)] or [(name, nbytes, dims)] in file order; each tensor's data is padded to
    `align`; dims default to (nbytes,)."""
    import struct

    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = (s("general.architecture") + struct.pack("<I", 8) + s(arch)
          + s("%s.block_count" % arch) + struct.pack("<I", 4) + struct.pack("<I", block_count)
          + s("general.alignment") + struct.pack("<I", 4) + struct.pack("<I", align))
    infos, off = b"", 0
    for t in tensors:
        name, n = t[0], t[1]
        dims = t[2] if len(t) > 2 else (n,)
        infos += (s(name) + struct.pack("<I", len(dims)) + struct.pack("<%dQ" % len(dims), *dims)
                  + struct.pack("<I", 0) + struct.pack("<Q", off))
        off += n + (-n) % align
    head = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), 3) + kv + infos
    head += b"\0" * ((-len(head)) % align)
    with open(path, "wb") as f:
        f.write(head + b"\0" * (off + tail))


class StagePlanning(unittest.TestCase):
    """Verify preflight memory checks against real free RAM, as rpc-server may report total RAM."""

    def test_the_split_is_llamacpps_own(self):
        # assignments llama.cpp logged ("layer N assigned to device"), rounds 3, 6 and 7
        self.assertEqual([len(u) for u in tv.stage_units([14, 3.5, 3.5, 3.5, 3.5], 28)], [15, 4, 3, 4, 3])
        self.assertEqual([len(u) for u in tv.stage_units([18, 11], 28)], [18, 11])
        r7 = tv.stage_units([1] + [2] * 8 + [12], 28)
        self.assertEqual([len(u) for u in r7], [1] + [2] * 8 + [12])
        self.assertEqual(r7[-1][-1], 28, "the output unit goes to the last stage")

    def test_gguf_bytes_come_from_offsets_and_alignment(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="gguf_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "m.gguf")
        write_gguf(p, [("token_embd.weight", 1000), ("blk.0.attn_q.weight", 40), ("blk.0.ffn_down.weight", 64),
                       ("blk.1.attn_q.weight", 40), ("output.weight", 500)], block_count=2, tail=12)
        n, tb = tv.gguf_tensor_bytes(p)
        self.assertEqual(n, 2)
        self.assertEqual(tb["token_embd.weight"], 1024, "padded to the 32-byte alignment, as the loader allocates")
        self.assertEqual(tb["blk.0.attn_q.weight"], 64)
        self.assertEqual(tb["output.weight"], 512 + 12, "the last tensor runs to the end of the file")

    def test_stage_weights_follow_the_split_and_the_output_unit(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="gguf_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        mib = 1048576
        p = os.path.join(d, "m.gguf")
        write_gguf(p, [("token_embd.weight", 3 * mib), ("blk.0.w", mib), ("blk.1.w", 2 * mib),
                       ("output_norm.weight", 32), ("output.weight", 4 * mib)], block_count=2)
        w = tv.stage_weight_mib(p, [{"dev": "gandalf", "ts": 1}, {"dev": "frodo", "ts": 1}, {"dev": "rigby", "ts": 1}])
        self.assertAlmostEqual(w["gandalf"], 1.0, places=3)
        self.assertAlmostEqual(w["frodo"], 2.0, places=3)
        self.assertAlmostEqual(w["rigby"], 4.0, places=3)
        tied = os.path.join(d, "tied.gguf")
        write_gguf(tied, [("token_embd.weight", 3 * mib), ("blk.0.w", mib), ("blk.1.w", mib)], block_count=2)
        w = tv.stage_weight_mib(tied, [{"dev": "frodo", "ts": 2}, {"dev": "rigby", "ts": 1}])
        self.assertAlmostEqual(w["rigby"], 3.0, places=3, msg="a tied model's output reuses the token table")

    def _hybrid(self):
        """Four layers, the last a full-attention layer (attn_k/attn_v 2048 -> 512 wide, the MoE's 2 KV heads x 256);
        the rest linear (attn_qkv, no KV cache) - the MoE's pattern in miniature."""
        import tempfile
        d = tempfile.mkdtemp(prefix="gguf_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "hybrid.gguf")
        ts = [("token_embd.weight", 64)]
        for il in range(3):
            ts.append(("blk.%d.attn_qkv.weight" % il, 64, (2048, 8192)))
        ts += [("blk.3.attn_k.weight", 64, (2048, 512)), ("blk.3.attn_v.weight", 64, (2048, 512)),
               ("output.weight", 64)]
        write_gguf(p, ts, block_count=4, arch="qwen35moe")
        return p

    def test_kv_cache_lives_only_on_attention_layers_and_grows_with_the_prompt(self):
        p = self._hybrid()
        self.assertEqual(tv.kv_bytes_per_token(p), (4, {3: 2048}))
        st = [{"dev": "frodo", "ts": 3}, {"dev": "gimli", "ts": 1}, {"dev": "rigby", "ts": 1}]
        # round 14 (r14 log, 4096 context): each single-attention-layer tablet "KV buffer size = 8.00 MiB"
        self.assertEqual(tv.stage_kv_mib(p, st, 4096), {"frodo": 0.0, "gimli": 8.0, "rigby": 0.0})
        self.assertEqual(tv.stage_kv_mib(p, st, 32768)["gimli"], 64.0)

    def test_a_model_whose_kv_cannot_be_sized_is_refused_not_read_as_empty(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="gguf_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        p = os.path.join(d, "fused.gguf")
        write_gguf(p, [("blk.0.attn_qkv.weight", 64, (2048, 6144)), ("output.weight", 64)], block_count=1)
        with self.assertRaises(ValueError):
            tv.kv_bytes_per_token(p)

    def test_bench_context_is_the_largest_test_padded_like_llamacpp(self):
        self.assertEqual(tv.bench_n_ctx({"p": [4096], "ub": 64}), 4096)
        self.assertEqual(tv.bench_n_ctx({"p": [4000], "n": [16]}), 4096)
        self.assertEqual(tv.bench_n_ctx({"p": [512, 32768]}), 32768)
        self.assertEqual(tv.bench_n_ctx({"p": [0], "n": [128], "d": [4096]}), 4352)

    def test_fit_needs_weights_plus_reserve_inside_real_free_memory(self):
        r = tv.STAGE_RESERVE_MIB
        self.assertEqual(tv.stage_memory_defects({"frodo": 372.0, "rigby": 8386.0}, {"frodo": 1071}), [])
        bad = tv.stage_memory_defects({"frodo": 860.0}, {"frodo": 1071})
        self.assertEqual(len(bad), 1)
        self.assertIn("frodo", bad[0])
        self.assertEqual(tv.stage_memory_defects({"frodo": 1071.0 - r}, {"frodo": 1071}), [])
        self.assertTrue(tv.stage_memory_defects({"frodo": 1071.0 - r + 1}, {"frodo": 1071}))

    def test_a_tablet_already_holding_the_server_is_not_sent_it_again(self):
        import hashlib
        import shutil
        import tempfile
        src = tempfile.mkdtemp(prefix="arm64_")
        self.addCleanup(shutil.rmtree, src, True)
        for name, body in (("ggml-rpc-server", b"server"), ("libomp.so", b"omp")):
            with open(os.path.join(src, name), "wb") as f:
                f.write(body)
        sha = {n: hashlib.sha256(b).hexdigest() for n, b in (("ggml-rpc-server", b"server"), ("libomp.so", b"omp"))}
        held = {"gandalf": "%s  ggml-rpc-server\n%s  libomp.so\n" % (sha["ggml-rpc-server"], sha["libomp.so"]),
                "frodo": "%s  ggml-rpc-server\n" % ("0" * 64)}
        sent = []
        with mock.patch.object(tf, "_ssh", side_effect=lambda h, cmd, timeout=None: (0, held.get(h, "")
                                                                                        if "sha256sum" in cmd else "")), \
                mock.patch.object(tf, "_scp", side_effect=lambda h, a, b, timeout=None: sent.append((h, b)) or (0, "")), \
                mock.patch.object(tv, "port_open", return_value=True), \
                mock.patch.object(tv, "rpc_server_name", return_value="ggml-rpc-server"):
            up = tv.start_backends(["gandalf", "frodo"], lambda m: None, src=src, tdir="llm-next")
        self.assertEqual(up, ["gandalf", "frodo"])
        self.assertEqual(sorted(sent), [("frodo", "llm-next/ggml-rpc-server"), ("frodo", "llm-next/libomp.so")],
                         "gandalf holds both files byte-identical; frodo's server differs and its libomp is missing")

    def test_the_cache_never_fills_a_tablets_disk(self):
        """Verify the cache pruning logic prevents disk exhaustion on low-space tablets."""
        import shutil
        import tempfile
        src = tempfile.mkdtemp(prefix="arm64_")
        self.addCleanup(shutil.rmtree, src, True)
        for name in ("ggml-rpc-server", "libomp.so"):
            with open(os.path.join(src, name), "wb") as f:
                f.write(b"x")
        free = {"gandalf": 900, "frodo": 6000}          # MiB free AFTER pruning, as the tablet reports it
        launched = {}

        def ssh(h, cmd, timeout=None):
            if "FREE $(free)" in cmd:
                want = int(re.search(r"-ge (\d+)", cmd).group(1))
                self.assertEqual(want, 400 + tv.CACHE_DISK_FLOOR_MIB, "room for the stage's weights + the floor")
                return 0, "FREE %d\n" % free[h]
            if "setsid" in cmd:
                launched[h] = " -c" in cmd
            return 0, ""
        import re
        with mock.patch.object(tf, "_ssh", side_effect=ssh), \
                mock.patch.object(tf, "_scp", return_value=(0, "")), \
                mock.patch.object(tv, "port_open", return_value=True), \
                mock.patch.object(tv, "rpc_server_name", return_value="ggml-rpc-server"):
            tv.start_backends(["gandalf", "frodo"], lambda m: None, src=src, tdir="llm-next", cache=True,
                              need_mib={"gandalf": 400, "frodo": 400})
        self.assertEqual(launched, {"gandalf": False, "frodo": True},
                         "a tablet that cannot fit the weights + floor runs uncached; one that can, caches")

    def test_an_unreadable_disk_is_never_cached_blind(self):
        with mock.patch.object(tf, "_ssh", return_value=(255, "ssh: connect failed")):
            self.assertEqual(tv.cache_room("pippin", 400), (False, None))

    def test_the_dram_probe_rides_tolkiens_own_transport(self):
        with mock.patch.object(tf, "_scp", return_value=(0, "")) as scp, \
                mock.patch.object(tf, "_ssh", return_value=(0, "4.67\n")) as ssh:
            self.assertEqual(tf.dram_health("frodo"), 4.67)
        self.assertEqual(scp.call_args[0][0], "frodo")
        with mock.patch.object(tf, "_scp", return_value=(1, "No route to host")):
            self.assertIsNone(tf.dram_health("pippin"), "unreachable = could not measure, never a number")

    def test_no_fresh_free_memory_is_could_not_tell_never_fits(self):
        bad = tv.stage_memory_defects({"sam": 100.0}, {})
        self.assertEqual(len(bad), 1)
        self.assertIn("could not read", bad[0])

    def test_free_memory_comes_from_the_telemetry_store(self):
        import sqlite3
        import tempfile
        import time as _t
        import tolkien_telemetry as tt
        d = tempfile.mkdtemp(prefix="telem_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        db = os.path.join(d, "t.sqlite")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE samples (ts REAL, node TEXT, mem_avail_mb REAL)")
        now = _t.time()
        c.executemany("INSERT INTO samples VALUES (?,?,?)", [(now - 500, "frodo", 1500), (now - 5, "frodo", 1071),
                                                               (now - 5, "sam", 1072), (now - 500, "merry", 800)])
        c.commit()
        c.close()
        with mock.patch.object(tt, "DB", db):
            got = tv.tablet_mem_avail({"frodo", "sam", "merry"})
            after = tv.tablet_mem_avail({"frodo", "sam"}, since=now - 10)
        self.assertEqual(got, {"frodo": 1071, "sam": 1072}, "newest fresh sample; a stale node is absent")
        self.assertEqual(after, {"frodo": 1071, "sam": 1072})
        with mock.patch.object(tt, "DB", db):
            self.assertEqual(tv.tablet_mem_avail({"frodo"}, since=now), {}, "nothing measured since then")

    def test_a_battery_under_the_floor_or_unread_refuses_the_stage(self):
        have = {"frodo": (50, "Discharging"), "sam": (19, "Charging")}
        bad = tv.stage_battery_defects(["frodo", "sam", "faramir", "rigby"], have)
        self.assertEqual(len(bad), 2, "frodo sits AT the floor and passes; rigby has no battery")
        self.assertIn("sam: battery 19% (Charging)", bad[0] if "sam" in bad[0] else bad[1])
        self.assertTrue(any("faramir: could not read its battery" in b for b in bad), "no reading is never 'fine'")

    def test_battery_comes_from_the_store_and_an_old_store_reads_as_could_not_tell(self):
        import sqlite3
        import tempfile
        import time as _t
        import tolkien_telemetry as tt
        d = tempfile.mkdtemp(prefix="telem_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        old, new = os.path.join(d, "old.sqlite"), os.path.join(d, "new.sqlite")
        c = sqlite3.connect(old)
        c.execute("CREATE TABLE samples (ts REAL, node TEXT, mem_avail_mb REAL)")
        c.execute("INSERT INTO samples VALUES (?,?,?)", (_t.time() - 5, "frodo", 1071))
        c.commit()
        c.close()
        with mock.patch.object(tt, "DB", old):
            self.assertEqual(tv.tablet_battery({"frodo"}), {}, "a store from before the column: could not tell")
        c = sqlite3.connect(new)
        c.execute("CREATE TABLE samples (ts REAL, node TEXT, battery_pct REAL, charging TEXT)")
        now = _t.time()
        c.executemany("INSERT INTO samples VALUES (?,?,?,?)", [(now - 500, "frodo", 90, "Full"),
                                                               (now - 9, "frodo", 61, "Discharging"),
                                                               (now - 3, "frodo", 60, "Discharging"),
                                                               (now - 3, "sam", 19, "Charging")])
        c.commit()
        c.close()
        with mock.patch.object(tt, "DB", new):
            self.assertEqual(tv.tablet_battery({"frodo", "sam", "merry"}),
                             {"frodo": (60, "Discharging"), "sam": (19, "Charging")})

    def test_the_telemetry_store_gains_new_columns_without_losing_rows(self):
        import sqlite3
        import tempfile
        import tolkien_telemetry as tt
        d = tempfile.mkdtemp(prefix="telem_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = os.path.join(d, "t.sqlite")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE samples (ts REAL, run TEXT, node TEXT, mem_avail_mb REAL, note TEXT)")
        c.execute("INSERT INTO samples (ts, node, mem_avail_mb) VALUES (1.0, 'frodo', 1071)")
        c.commit()
        c.close()
        db = tt.connect(path)
        self.addCleanup(db.close)
        cols = {r[1] for r in db.execute("PRAGMA table_info(samples)")}
        self.assertTrue(set(tt.COLUMNS) <= cols, "every logged column exists after connect")
        self.assertEqual(db.execute("SELECT node, mem_avail_mb, battery_pct FROM samples").fetchall(),
                         [("frodo", 1071.0, None)])
        db.execute("INSERT INTO samples (ts, node, battery_pct, charging) VALUES (2.0, 'sam', 19, 'Charging')")
        tt.connect(path).close()                                  # a second connect adds nothing and breaks nothing

    def test_the_tablet_sample_reads_battery_percent_and_status(self):
        import tolkien_telemetry as tt
        text = "100.5 200.1\nMemAvailable:    1048576 kB\nT 31000\nB 7 Charging\nInter-|   Receive\n face |bytes\n"
        s = tt.parse_tablet(text)
        self.assertEqual((s["battery"], s["charging"]), (7, "Charging"))
        self.assertEqual(tt.battery_status("B 83 Not charging\n"), (83, "Not charging"))
        self.assertEqual(tt.battery_status("B \n"), (None, None), "no capacity file: could not tell, never 0 %")
        self.assertEqual(tt.battery_status(""), (None, None))
        self.assertEqual(tt.battery_status("B 140 Full\n"), (None, "Full"), "an impossible percent is not a reading")



def real_trace():
    """Every RPCTRACE line of a real run, verbatim (tests/rpctrace_fixture.json says which)."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpctrace_fixture.json"), encoding="utf-8") as f:
        return "\n".join(json.load(f)["lines"]) + "\n"


class TraceSummary(unittest.TestCase):
    """#36 round 2: name the slow stage. Pinned on the real trace of a 7B split over two localhost rpc-servers
    (512 tokens in 64-token chunks = 8 per pass, warmup + 1 timed pass = 16 chunks per stage, ~46 s per pass)."""

    def test_every_trace_line_is_read_including_the_ones_behind_load_dots(self):
        text = real_trace()
        self.assertEqual(len(tv._TRACE_LINE.findall(text)), 1672)
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith(".")), 82, "the fixture still holds them")

    def test_both_stages_and_every_chunk_are_found(self):
        s = tv.trace_summary(real_trace())
        self.assertEqual(sorted(s), ["127.0.0.1:50091", "127.0.0.1:50092"])
        self.assertEqual([s[k]["computes"] for k in sorted(s)], [16, 16])
        self.assertEqual(s["127.0.0.1:50091"]["windows"], 16, "a middle stage is read after every chunk")
        self.assertEqual(s["127.0.0.1:50092"]["windows"], 2, "the last stage is read once per pass")

    def test_per_chunk_times_match_the_measured_pass(self):
        s = tv.trace_summary(real_trace())
        for k in s:
            self.assertTrue(4500 < s[k]["median_ms"] < 6500, (k, s[k]))
            self.assertGreater(s[k]["busy_share"], 0.9, "the two stages overlapped: each busy nearly all the time")

    def test_waiting_for_upstream_is_not_counted_as_copy_time(self):
        s = tv.trace_summary(real_trace())
        # the posted copy into stage 2 sits ~4-5 s per chunk waiting for stage 1; localhost copies themselves are ~ms
        self.assertLess(s["127.0.0.1:50092"]["copy_in_s"], 0.5)
        self.assertGreater(s["127.0.0.1:50092"]["copies_in"], 0)

    def test_weight_uploads_before_the_first_compute_are_not_hand_offs(self):
        s = tv.trace_summary(real_trace())
        self.assertLess(s["127.0.0.1:50091"]["copy_in_s"], 0.5, "the 38 MB weight SETs happen while loading")

    def test_a_trace_without_host_wait_lines_reports_zero_waits(self):
        s = tv.trace_summary(real_trace())          # captured before patch 4 existed
        self.assertEqual([s[k]["host_waits"] for k in sorted(s)], [0, 0])

    def test_host_waits_count_only_once_computing_began(self):
        # real patch-4 trace, graph reuse off: 85 waits while loading weights, then each server's 2 end-of-pass syncs
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpctrace_hostwait_fixture.json"),
                  encoding="utf-8") as f:
            text = "\n".join(json.load(f)["lines"]) + "\n"
        self.assertEqual(len(tv._HOSTWAIT_LINE.findall(text)), 89, "the matcher sees every wait line")
        s = tv.trace_summary(text)
        self.assertEqual({k: s[k]["host_waits"] for k in s}, {"127.0.0.1:50083": 2, "127.0.0.1:50084": 2})
        self.assertGreater(s["127.0.0.1:50083"]["host_wait_s"], 10, "the host sat in the first server's end-of-pass sync")

    def test_no_trace_lines_is_an_error_never_an_empty_summary(self):
        with self.assertRaises(RuntimeError):
            tv.trace_summary("llama_context: pipeline parallelism enabled\n")


class OverlapVerdict(unittest.TestCase):
    """llama-bench must be run with -v; otherwise llama.cpp logs are silenced and overlap detection fails."""

    def test_three_states(self):
        self.assertTrue(tv.overlap_verdict("llama_context: n_ctx = 2048\nllama_context: pipeline parallelism enabled\n"))
        self.assertIs(tv.overlap_verdict("llama_context: n_ctx = 2048\nllama_context: graph nodes = 900\n"), False)
        self.assertIsNone(tv.overlap_verdict(""), "no llama log at all = could not tell, never False")

    def test_stage_runs_are_verbose(self):
        eps = {"rigby": "127.0.0.1:50061", "gandalf": "192.0.2.16:50062", "frodo": "192.0.2.11:50062"}
        dm = {"card": "Vulkan0", "rigby": "RPC0", "gandalf": "RPC1", "frodo": "RPC2"}
        head, _env = tv.bench_head(STAGED)
        self.assertIn("-v", tv.stage_argv(STAGED, head, "/models/m.gguf", eps, dm))


class LiveStats(unittest.TestCase):
    """LiveStats polls a running process; it must not treat 'no output yet' as failure."""

    def test_a_run_in_flight_beats(self):
        proc = mock.Mock()
        proc.poll.side_effect = [None, None, 0, 0]
        proc.returncode = 0
        proc.stdout.read.return_value = "[]"
        proc.stderr.read.return_value = ""
        beats = []
        with mock.patch.object(tv.subprocess, "Popen", return_value=proc), \
             mock.patch.object(tv, "BENCH_BEAT_S", 0), mock.patch.object(tv.time, "sleep"):
            tv.run_stoppable(["llama-bench"], {}, None, beat=beats.append)
        self.assertGreaterEqual(len(beats), 1)

    JSONL = ('{"n_prompt": 4096, "n_gen": 0, "n_depth": 0, "avg_ts": 87.71, "stddev_ts": 0.4, "samples_ts": [87.3, 88.1]}\n'
             '{"n_prompt": 0, "n_gen": 64, "n_depth": 16384, "avg_ts": 8.43, "stddev_ts": 0.1, "samples_ts": [8.4, 8.46]}\n')

    def test_each_finished_test_puts_its_speed_on_the_deck(self):
        lines = []
        beat, on_line = tv.bench_live(lambda rid, text: lines.append((rid, text)), "r1", 2)
        for l in self.JSONL.splitlines():
            on_line(l + "\n")
        on_line("not json\n")
        self.assertEqual(lines[0], ("r1", "pp4096 87.7 tok/s (1/2 done)"))
        self.assertEqual(lines[1], ("r1", "tg64 @d16384 8.4 tok/s (2/2 done)"))
        self.assertEqual(len(lines), 2, "a line that is not a result is no deck update")
        beat(125)
        self.assertEqual(lines[-1], ("r1", "2m05s | tg64 @d16384 8.4 tok/s (2/2 done)"))

    def test_the_beat_before_any_result_is_elapsed_only(self):
        lines = []
        beat, _ = tv.bench_live(lambda rid, text: lines.append(text), "r1", 2)
        beat(61)
        self.assertEqual(lines, ["1m01s"])

    def test_run_stoppable_hands_over_lines_and_still_returns_all_the_text(self):
        import io
        proc = mock.Mock()
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = io.StringIO(self.JSONL)
        proc.stderr = io.StringIO("")
        got = []
        with mock.patch.object(tv.subprocess, "Popen", return_value=proc):
            rc, out, _err = tv.run_stoppable(["llama-bench"], {}, None, on_line=got.append)
        self.assertEqual((rc, out, len(got)), (0, self.JSONL, 2))

    def test_a_failing_line_callback_costs_no_output(self):
        import io
        proc = mock.Mock()
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = io.StringIO(self.JSONL)
        proc.stderr = io.StringIO("")
        def boom(_l):
            raise ValueError("x")
        with mock.patch.object(tv.subprocess, "Popen", return_value=proc):
            _rc, out, _err = tv.run_stoppable(["llama-bench"], {}, None, on_line=boom)
        self.assertEqual(out, self.JSONL)

    def test_parse_bench_reads_jsonl_and_the_samples_arrays_do_not_confuse_it(self):
        res = tv.parse_bench(self.JSONL, 0)
        self.assertEqual([x["avg_ts"] for x in res], [87.71, 8.43])
        self.assertEqual(len(tv.parse_bench(bench_json(3), 0)), 3, "the -o json array form still parses")

    def _telemetry(self, rows):
        import tempfile
        import time as _t
        import tolkien_telemetry as tt
        path = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        db = tt.connect(path)
        now = _t.time()
        for node, pct in rows:
            db.execute("INSERT INTO samples (ts, run, node, proc, proc_cpu_pct) VALUES (?, '', ?, 'ggml-rpc-server', ?)",
                       (now, node, pct))
        db.commit()
        db.close()
        return mock.patch.object(tt, "DB", path)

    def test_the_deck_line_comes_from_the_telemetry_store(self):
        with self._telemetry([("gandalf", 700.0), ("frodo", 650.0), ("sam", 20.0)]):
            line = tv.stage_busy_now({"gandalf", "frodo", "sam", "gimli"})
        self.assertTrue(line.startswith("busy now 2/4: gandalf 700%"), line)
        self.assertNotIn("gimli", line, "a stage with no recent row is not shown as idle")

    def test_overlap_over_a_run_window_comes_from_the_same_store(self):
        import time as _t
        with self._telemetry([("gandalf", 700.0), ("frodo", 650.0)]):
            self.assertEqual(tv.stage_overlap(_t.time() - 60, _t.time() + 60)[0], 2)

    def test_no_store_is_could_not_tell_never_idle(self):
        import tolkien_telemetry as tt
        with mock.patch.object(tt, "DB", os.path.join(os.path.dirname(__file__), "no", "such.sqlite")):
            self.assertIsNone(tv.stage_busy_now({"gandalf"}))
            self.assertIsNone(tv.stage_overlap(0, 1))


class StopMidRun(unittest.TestCase):
    def test_run_stoppable_terminates_the_process_when_the_stop_is_set(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.stdout.read.return_value = ""
        proc.stderr.read.return_value = ""
        ev = threading.Event()
        ev.set()
        with mock.patch.object(tv.subprocess, "Popen", return_value=proc):
            with self.assertRaises(tv.BenchStopped):
                tv.run_stoppable(["llama-bench"], {}, ev)
        proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
