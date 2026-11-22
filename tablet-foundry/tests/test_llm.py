"""`llm` order kind: one model served by Tolkien, on rigby alone or as Voltron (rigby + the wire tablets as one
machine), graded by the tablets' scorer. No process, socket or tablet is touched."""
import json
import os
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf
import tolkien_llm as tv

GOOD = "```python\ndef add(a, b):\n    return a + b\n```"
BAD = "```python\ndef add(a, b):\n    return a - b\n```"


def fake_payload():
    m = types.ModuleType("fake_payload")
    m.SUFFIX = "\nReturn only code."
    m.TEMPERATURE = 0.6
    m.CONTEXT = 2048
    m.TASKS = {"add": {"prompt": "write add", "checks": ["lambda: add(2, 3) == 5"]}}
    m.CONTROL_TASKS = {"control_add": {"prompt": "control add", "checks": ["lambda: add(1, 1) == 2"]}}
    m.extract_code = lambda text: text.split("```python\n")[1].split("```")[0] if "```python" in text else ""

    def score(code, checks, work):
        ns = {}
        exec(code, ns)
        passed = sum(1 for c in checks if eval(c, ns)())
        return passed, len(checks), ""
    m.score = score
    return m


SPEC = {"kind": "llm", "name": "v1", "model": "m.gguf", "payload": "/p.py", "tasks": ["add"], "replicates": 2}

_STATE = mock.patch.object(tf, "STATE_DIR", None)


def setUpModule():
    import tempfile
    _STATE.new = tempfile.mkdtemp(prefix="llm_state_")
    _STATE.start()


def tearDownModule():
    import shutil
    _STATE.stop()
    shutil.rmtree(_STATE.new, ignore_errors=True)


class FreshOrder(unittest.TestCase):
    """Every test that runs an arm starts with no checkpoint: run_arm resumes from one, and these tests reuse a name."""

    def setUp(self):
        for f in os.listdir(tf.STATE_DIR):
            if ".checkpoint.jsonl" in f:
                os.remove(os.path.join(tf.STATE_DIR, f))


class Vocabulary(unittest.TestCase):
    def test_voltron_names_only_the_pooled_arm(self):
        self.assertEqual(tv.SHAPE, {"rigby": "rigby", "voltron": "S5-voltron", "warm": "S1-warm"})
        self.assertEqual(tv.normalize({"kind": "llm"})["arms"], ["rigby", "voltron"], "warm is never a default arm")

    def test_legacy_orders_are_read_in_todays_words(self):
        s = tv.normalize(dict(SPEC, kind="voltron", arms=["local", "pooled"]))
        self.assertEqual((s["kind"], s["arms"]), ("llm", ["rigby", "voltron"]))

    def test_only_a_voltron_arm_uses_the_tablets(self):
        self.assertFalse(tv.uses_tablets(dict(SPEC, arms=["rigby"])))
        self.assertFalse(tv.uses_tablets(dict(SPEC, kind="voltron", arms=["local"])))
        self.assertTrue(tv.uses_tablets(dict(SPEC, arms=["rigby", "voltron"])))


class Validation(unittest.TestCase):
    def test_a_good_spec_has_no_defects(self):
        self.assertEqual(tf.spec_defects(SPEC), [])
        self.assertEqual(tf.spec_defects(dict(SPEC, kind="voltron", arms=["local"])), [])

    def test_defects_are_named(self):
        d = tf.spec_defects(dict(SPEC, tasks=[], arms=["remote"], replicates=0))
        self.assertTrue(any("tasks" in x for x in d))
        self.assertTrue(any("arms" in x for x in d))
        self.assertTrue(any("replicates" in x for x in d))


class ServerArgv(unittest.TestCase):
    def test_rigby_keeps_every_layer_on_rigby(self):
        a = tv.server_argv("/models/m.gguf", 2048)
        self.assertNotIn("--rpc", a)
        self.assertNotIn("-ngl", a)

    def test_voltron_names_every_backend_and_offloads_all_layers(self):
        # without -ngl 99 llama-server keeps every layer on rigby and the tablets idle
        a = tv.server_argv("/models/m.gguf", 2048, ["192.0.2.11:50052", "192.0.2.16:50052"])
        self.assertEqual(a[a.index("--rpc") + 1], "192.0.2.11:50052,192.0.2.16:50052")
        self.assertEqual(a[a.index("-ngl") + 1], "99")


class SpeculativeDraft(FreshOrder):
    def test_a_draft_model_reaches_the_server_command(self):
        a = tv.server_argv("/models/big.gguf", 2048, draft="/models/small.gguf")
        self.assertEqual(a[a.index("--model-draft") + 1], "/models/small.gguf")
        self.assertNotIn("--model-draft", tv.server_argv("/models/big.gguf", 2048))

    def test_a_draft_model_turns_speculation_on(self):
        # llama.cpp defaults --spec-type to none; a draft model is inert without this flag
        a = tv.server_argv("/models/big.gguf", 2048, draft="/models/small.gguf")
        self.assertEqual(a[a.index("--spec-type") + 1], "draft-simple")
        self.assertNotIn("--spec-type", tv.server_argv("/models/big.gguf", 2048))

    def test_speculation_without_a_draft_model(self):
        a = tv.server_argv("/models/big.gguf", 2048, spec_type="ngram-simple")
        self.assertEqual(a[a.index("--spec-type") + 1], "ngram-simple")
        self.assertNotIn("--model-draft", a)
        self.assertEqual(tf.spec_defects(dict(SPEC, spec_type="ngram-simple")), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, spec_type="warp-drive")))
        self.assertTrue(tf.spec_defects(dict(SPEC, spec_type="draft-simple")))       # needs a draft_model

    def test_a_draft_model_is_a_file_name_in_the_models_mount(self):
        self.assertEqual(tf.spec_defects(dict(SPEC, draft_model="small.gguf")), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, draft_model="/etc/passwd")))
        self.assertTrue(tf.spec_defects(dict(SPEC, draft_model="")))

    def test_the_draft_applies_to_the_rigby_arm_only(self):
        seen = {}
        mod = fake_payload()
        with mock.patch.object(tv, "start_server",
                               side_effect=lambda *a, **k: seen.setdefault(len(seen), k.get("draft")) and None
                               or ("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", return_value=(GOOD, 1.0, {})), \
             mock.patch.object(tv, "start_backends", side_effect=lambda h, log: list(h)), \
             mock.patch.object(tv, "kill_backends", return_value=[]):
            spec = dict(SPEC, draft_model="small.gguf")
            r_rows, r_meta = tv.run_arm("rigby", spec, mod, "/tmp", [], lambda m: None, lambda *a: None)
            v_rows, v_meta = tv.run_arm("voltron", spec, mod, "/tmp", ["frodo"], lambda m: None, lambda *a: None,
                                        adb_sh=lambda *a, **k: "")
        self.assertEqual(seen[0], os.path.join(tv.MODELS, "small.gguf"))
        self.assertIsNone(seen[1])
        self.assertEqual(r_meta["draft_model"], "small.gguf")
        self.assertIsNone(v_meta["draft_model"])
        self.assertEqual({r["draft_model"] for r in r_rows}, {"small.gguf"})


DRAFTED = dict(SPEC, arms=["rigby"], draft_model="small.gguf", draft_host="gandalf")


class DraftOnATablet(FreshOrder):
    """#36 job 2: the draft model on one tablet's rpc-server, the target's layers on rigby's CPU."""

    def test_a_drafted_order_validates_and_takes_the_tablet_lane(self):
        self.assertEqual(tf.spec_defects(DRAFTED), [])
        self.assertTrue(tv.uses_tablets(DRAFTED))
        self.assertEqual(tv.lane_of_spec(DRAFTED), "fleet")

    def test_a_drafted_order_names_its_defects(self):
        for bad in (dict(DRAFTED, draft_host="mordor"), dict(DRAFTED, draft_model=None),
                    dict(DRAFTED, arms=["rigby", "voltron"]), dict(DRAFTED, spec_type="ngram-simple"),
                    dict(DRAFTED, gpu_layers=99)):
            self.assertTrue(tf.spec_defects(bad), bad)

    def test_the_target_stays_on_rigby_and_only_the_draft_goes_to_the_tablet(self):
        a = tv.server_argv("/models/big.gguf", 2048, draft="/models/small.gguf",
                           draft_rpc=("192.0.2.16:50052", "RPC0[192.0.2.16:50052]"))
        self.assertEqual(a[a.index("--rpc") + 1], "192.0.2.16:50052")
        self.assertEqual(a[a.index("--device") + 1], "none")         # -ngl auto would fit the target onto the tablet
        self.assertEqual(a[a.index("-ngl") + 1], "0")
        self.assertEqual(a[a.index("--device-draft") + 1], "RPC0[192.0.2.16:50052]")
        self.assertEqual(a[a.index("-ngld") + 1], "99")
        self.assertEqual(a[a.index("--spec-type") + 1], "draft-simple")

    def test_rpc_device_names_are_read_in_both_spellings(self):
        out = ("Available devices:\n  RPC0[192.0.2.16:50052]: RPC (2700 MiB, 2600 MiB free)\n"
               "  RPC1: RPC (2700 MiB, 2600 MiB free)\n")
        self.assertEqual(tv.rpc_devices(out), ["RPC0[192.0.2.16:50052]", "RPC1"])
        self.assertEqual(tv.rpc_devices("Available devices:\n"), [])

    def _run(self, devices=("RPC0[192.0.2.16:50052]",), load_error=None):
        seen, killed = {}, []
        start = mock.patch.object(tv, "start_server", side_effect=load_error or (
            lambda *a, **k: seen.update(k) or ("proc", 1.0)))
        with start, mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", return_value=(GOOD, 1.0, {})), \
             mock.patch.object(tv, "start_backends", side_effect=lambda h, log, all_cores=False:
                               seen.update(all_cores=all_cores) or list(h)), \
             mock.patch.object(tv, "kill_backends", side_effect=lambda h, adb: killed.append(list(h)) or []), \
             mock.patch.object(tv, "list_rpc_devices", return_value=(list(devices), "tail")):
            rows, meta = tv.run_arm("rigby", DRAFTED, fake_payload(), "/tmp", ["gandalf"], lambda m: None,
                                    lambda *a: None, adb_sh=lambda *a, **k: "")
        return rows, meta, seen, killed

    def test_the_drafter_starts_on_every_core_and_is_killed_after(self):
        with mock.patch.object(tf, "WIRE_PREFIX", "192.0.2."):      # the wire subnet is a setting; a documentation one
            rows, meta, seen, killed = self._run()
        self.assertEqual(seen["draft_rpc"], ("192.0.2.%d:50052" % tf.WIRE_OCTET["gandalf"], "RPC0[192.0.2.16:50052]"))
        self.assertTrue(seen["all_cores"])
        self.assertEqual(killed, [["gandalf"], ["gandalf"]])           # orphans first, then cleanup
        self.assertEqual((meta["draft_host"], rows[1]["draft_host"], rows[1]["nodes"]), ("gandalf", "gandalf", 0))

    def test_anything_but_exactly_one_rpc_device_refuses_to_start_and_still_cleans_up(self):
        for devs in ((), ("RPC0", "RPC1")):
            rows, meta, seen, killed = self._run(devices=devs)
            self.assertEqual(rows, [])
            self.assertIn("exactly 1 RPC device", meta["error"])
            self.assertNotIn("draft_rpc", seen)
            self.assertEqual(killed[-1], ["gandalf"])

    def test_the_drafter_is_killed_over_the_usb_wire_first(self):
        seen = []
        with mock.patch.object(tf, "node_ip", side_effect=lambda h, port, refresh=False: ("192.0.2.16", "wire")), \
             mock.patch.object(tv, "port_open", return_value=False), mock.patch.object(tv.time, "sleep"):
            left = tv.kill_backends(["gandalf"], lambda ip, cmd, timeout=0: seen.append(ip))
        self.assertEqual((seen, left), (["192.0.2.16"], []))

    def test_a_server_that_dies_loading_still_kills_the_drafter(self):
        rows, meta, seen, killed = self._run(load_error=RuntimeError("exited rc=1 while loading"))
        self.assertIn("while loading", meta["error"])
        self.assertEqual(len(killed), 2)


class GpuPlacement(FreshOrder):
    """rigby's GT 730: a placement (-ngl + -ot) is served by the host's Vulkan build with Kepler's hang-avoiding env."""
    OT = [r"blk\.(?!(3|7|11|15|19|23|27|31|35|39)\.).*=CPU", "ffn_=CPU", r"output\.=CPU"]

    def test_a_placement_reaches_the_vulkan_server_in_order(self):
        a = tv.server_argv("/models/moe.gguf", 32768, gpu_layers=99, override_tensor=self.OT)
        # the 470 driver starts only under the host's loader and libraries (measured in throwaway containers)
        self.assertEqual(a[:4], [os.path.join(tv.HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path",
                                 "%s:%s" % (tv.LLAMA_VULKAN, tv.HOSTLIB),
                                 os.path.join(tv.LLAMA_VULKAN, "llama-server")])
        self.assertEqual(a[a.index("-ngl") + 1], "99")
        self.assertEqual([a[i + 1] for i, x in enumerate(a) if x == "-ot"], self.OT)

    def test_no_placement_is_the_cpu_build_unchanged(self):
        a = tv.server_argv("/models/moe.gguf", 32768)
        self.assertEqual(a[0], os.path.join(tv.LLAMA_BIN, "llama-server"))
        self.assertNotIn("-ot", a)
        self.assertNotIn("GGML_VK_DISABLE_F16", tv.server_env())
        self.assertEqual(tv.server_env()["LD_LIBRARY_PATH"], tv.LLAMA_BIN)

    def test_the_gpu_env_disables_keplers_hanging_shader_paths(self):
        env = tv.server_env(99)
        self.assertNotIn("LD_LIBRARY_PATH", env)          # the image's libs must not mix with the host's glibc
        for k in ("GGML_VK_DISABLE_F16", "GGML_VK_DISABLE_COOPMAT", "GGML_VK_DISABLE_COOPMAT2"):
            self.assertEqual(env[k], "1")

    def test_op_offload_off_keeps_prompt_batches_on_the_cpu(self):
        a = tv.server_argv("/models/moe.gguf", 32768, gpu_layers=99, override_tensor=self.OT, op_offload=False)
        self.assertIn("--no-op-offload", a)
        self.assertNotIn("--no-op-offload", tv.server_argv("/models/moe.gguf", 32768, gpu_layers=99))
        self.assertNotIn("--no-op-offload", tv.server_argv("/models/moe.gguf", 32768, op_offload=False))  # CPU build
        self.assertEqual(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu_layers=99, op_offload=False)), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, arms=["rigby"], op_offload=False)))      # needs gpu_layers
        self.assertTrue(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu_layers=99, op_offload="no")))

    def test_the_gtx_1060_is_placed_by_llama_cpps_fitter_on_the_named_card(self):
        nxt = tv.next_dirs()["vulkan"]
        a = tv.server_argv("/models/moe.gguf", 32768, vk_dev="Vulkan0", fit=(1024, 32768), vk_dir=nxt)
        self.assertEqual(a[:4], [os.path.join(tv.HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path",
                                 "%s:%s" % (nxt, tv.HOSTLIB), os.path.join(nxt, "llama-server")])
        self.assertEqual(a[a.index("-dev") + 1], "Vulkan0")
        self.assertEqual(a[a.index("-sm") + 1], "none")
        self.assertEqual((a[a.index("-fitt") + 1], a[a.index("-fitc") + 1]), ("1024", "32768"))
        for hand in ("-ngl", "-ot", "--no-op-offload"):
            self.assertNotIn(hand, a, "the fitter places; a hand flag would switch it off")

    def test_parallel_is_opt_in_and_names_the_slot_count(self):
        self.assertNotIn("-np", tv.server_argv("/models/moe.gguf", 32768), "unset keeps every earlier run comparable")
        a = tv.server_argv("/models/moe.gguf", 32768, parallel=1)
        self.assertEqual(a[a.index("-np") + 1], "1")
        ok = dict(SPEC, arms=["rigby"], parallel=1)
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in (0, -1, True, "1"):
            self.assertTrue(tf.spec_defects(dict(ok, parallel=bad)), bad)
        self.assertTrue(tf.spec_defects(dict(ok, arms=["voltron"])))

    def test_ubatch_is_opt_in_and_the_logical_batch_holds_it(self):
        self.assertNotIn("-ub", tv.server_argv("/models/moe.gguf", 32768))
        a = tv.server_argv("/models/moe.gguf", 32768, ubatch=2048)
        self.assertEqual(a[a.index("-ub") + 1], "2048")
        self.assertNotIn("-b", a, "the server's default -b 2048 already holds a 2048 micro-batch")
        a = tv.server_argv("/models/moe.gguf", 32768, ubatch=4096)
        self.assertEqual((a[a.index("-ub") + 1], a[a.index("-b") + 1]), ("4096", "4096"))
        ok = dict(SPEC, arms=["rigby"], ubatch=2048)
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in (0, True, "2048"):
            self.assertTrue(tf.spec_defects(dict(ok, ubatch=bad)), bad)

    def test_two_cards_are_a_layer_split_with_a_margin_each(self):
        a = tv.server_argv("/models/moe.gguf", 32768, vk_dev="Vulkan0,Vulkan1", fit=("1024,256", 32768),
                           vk_dir=tv.next_dirs()["vulkan"])
        self.assertEqual(a[a.index("-dev") + 1], "Vulkan0,Vulkan1")
        self.assertEqual(a[a.index("-sm") + 1], "layer")
        self.assertEqual(a[a.index("-fitt") + 1], "1024,256")
        one = tv.server_argv("/models/moe.gguf", 32768, vk_dev="Vulkan0", fit=("1024", 32768))
        self.assertEqual(one[one.index("-sm") + 1], "none")

    def test_card_lists_and_margins_are_validated(self):
        ok = dict(SPEC, arms=["rigby"], gpu=["gtx1060", "gt730"], fit_target=[512, 256], build="next")
        self.assertEqual(tf.spec_defects(ok), [])
        self.assertEqual(tf.spec_defects(dict(ok, fit_target=512)), [], "one margin applies to every card")
        self.assertTrue(tf.spec_defects(dict(ok, fit_target=[512])), "a list needs one margin per card")
        self.assertTrue(tf.spec_defects(dict(ok, gpu=["gtx1060", "gtx1060"])))
        self.assertTrue(tf.spec_defects(dict(ok, gpu=[])))
        self.assertTrue(tf.spec_defects(dict(ok, gpu=["gtx1060", "rtx5090"])))
        self.assertTrue(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu=["gtx1060", "gt730"], gpu_layers=20)))
        self.assertEqual(tf.spec_defects(dict(ok, gpu="gtx1060", fit_target=512, kepler_flags=True)), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, arms=["rigby"], kepler_flags=True)), "the flags need a placement")

    def test_kepler_flags_follow_the_730_or_the_control_arm(self):
        self.assertEqual(tv.server_env(None, ("1024,256", 4096), ["gtx1060", "gt730"])["GGML_VK_DISABLE_F16"], "1",
                         "process-wide: with the 730 in the placement the 1060 runs under them too")
        self.assertEqual(tv.server_env(None, ("1024", 4096), ["gtx1060"], True)["GGML_VK_DISABLE_F16"], "1")
        self.assertIsNone(tv.server_env(None, ("1024", 4096), ["gtx1060"]).get("GGML_VK_DISABLE_F16"))

    def test_the_fitter_report_runs_fit_params_with_the_launch_arguments(self):
        launch = tv.server_argv("/models/moe.gguf", 32768, vk_dev="Vulkan0", fit=("1024", 32768),
                                vk_dir=tv.next_dirs()["vulkan"], parallel=1, ubatch=2048, reasoning="off")
        got = {}

        def run(argv, **kw):
            got["argv"] = argv
            # mock output must match the real report format: header line followed by the memory table
            return mock.Mock(returncode=0, stdout="Vulkan0 3900 120 1450\nHost 12100 0 300\n-ngl 41 -ot exps=CPU\n",
                             stderr="I llama_fit_params: printing estimated memory in MiB to stdout (device, model, "
                                    "context, compute) ...\n\n")
        with mock.patch.object(tv.subprocess, "run", side_effect=run):
            rep = tv.fit_placement(launch, {})
        a = got["argv"]
        self.assertTrue(any(x.endswith("/llama-fit-params") for x in a))
        self.assertFalse(any(x.endswith("/llama-server") for x in a))
        for flag in ("--host", "--port", "--reasoning"):
            self.assertNotIn(flag, a, "server-only flags are not the fitter's")
        for flag in ("-np", "-ub", "-dev", "-fitt", "-fitc", "-c"):
            self.assertIn(flag, a, "everything that changes memory reaches the fitter")
        self.assertEqual(a[-2:], ["-fitp", "on"])
        for line in ("Vulkan0 3900 120 1450", "Host 12100 0 300", "-ngl 41 -ot exps=CPU"):
            self.assertIn(line, rep["lines"], "the memory table and the fitted arguments are the report - keep them")
        self.assertNotIn("", rep["lines"])

    def test_a_fitter_report_that_cannot_run_says_so(self):
        with mock.patch.object(tv.subprocess, "run", side_effect=OSError("no such file")):
            rep = tv.fit_placement(["/x/llama-server", "-m", "m"], {})
        self.assertIn("OSError", rep["error"])

    def test_threads_and_load_mode_reach_the_server(self):
        a = tv.server_argv("/models/moe.gguf", 32768)
        self.assertEqual(a[a.index("-t") + 1], str(tv.THREADS))
        self.assertNotIn("--load-mode", a, "unset keeps every earlier run comparable")
        a = tv.server_argv("/models/moe.gguf", 32768, threads=8, load_mode="none")
        self.assertEqual((a[a.index("-t") + 1], a[a.index("--load-mode") + 1]), ("8", "none"))
        self.assertEqual(tv.server_env(None, None, op_offload_min_batch=256)["GGML_OP_OFFLOAD_MIN_BATCH"], "256")
        self.assertNotIn("GGML_OP_OFFLOAD_MIN_BATCH", tv.server_env())

    def test_draft_max_uses_each_spec_types_own_flag(self):
        def tail(spec_type, n):
            a = tv.server_argv("/models/moe.gguf", 32768, spec_type=spec_type, draft_max=n)
            return a[a.index("--spec-type"):]
        self.assertEqual(tail("ngram-simple", 8), ["--spec-type", "ngram-simple", "--spec-ngram-simple-size-m", "8",
                                                   "--spec-ngram-simple-size-n", "8"])
        self.assertEqual(tail("ngram-map-k", 8), ["--spec-type", "ngram-map-k", "--spec-ngram-map-k-size-m", "8",
                                                  "--spec-ngram-map-k-size-n", "8"])
        self.assertEqual(tail("ngram-mod", 8),
                         ["--spec-type", "ngram-mod", "--spec-ngram-mod-n-max", "8", "--spec-ngram-mod-n-min", "8"],
                         "the mod type's minimum (default 48) may not exceed its maximum")
        self.assertEqual(tail("draft-mtp", 2), ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2"])
        self.assertNotIn("--spec-ngram-simple-size-m",
                         tv.server_argv("/models/moe.gguf", 32768, spec_type="ngram-simple"))

    def test_an_ngram_draft_is_never_shorter_than_its_lookup(self):
        # Enforce size-m >= lookup: if draft max < min, llama.cpp refuses all drafts (ngram-map.cpp logic).
        for spec_type in ("ngram-simple", "ngram-map-k", "ngram-mod"):
            for n in range(1, 65):
                a = tv.draft_max_args(spec_type, n)
                length, floor = int(a[1]), int(a[3])
                self.assertEqual(length, n)
                self.assertLessEqual(floor, length, (spec_type, n, a))

    def test_the_new_rigby_knobs_are_validated(self):
        ok = dict(SPEC, arms=["rigby"], threads=8, load_mode="none", spec_type="ngram-simple", draft_max=8,
                  op_offload_min_batch=256)
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in (dict(ok, threads=0), dict(ok, threads=True), dict(ok, load_mode="nommap"),
                    dict(ok, draft_max=0), dict(ok, op_offload_min_batch=0),
                    dict({k: v for k, v in ok.items() if k != "spec_type"})):
            self.assertTrue(tf.spec_defects(bad), bad)

    def test_the_fitter_report_drops_speculation_flags(self):
        launch = tv.server_argv("/models/moe.gguf", 32768, vk_dev="Vulkan0", fit=("1024", 32768),
                                spec_type="ngram-mod", draft_max=8, load_mode="none")
        got = {}

        def run(argv, **kw):
            got["argv"] = argv
            return mock.Mock(returncode=0, stdout="Vulkan0 1 2 3\n", stderr="")
        with mock.patch.object(tv.subprocess, "run", side_effect=run):
            tv.fit_placement(launch, {})
        a = got["argv"]
        self.assertFalse([x for x in a if x.startswith("--spec-")])
        self.assertNotIn("ngram-mod", a, "a dropped flag takes its value with it")
        self.assertEqual(a[a.index("--load-mode") + 1], "none", "memory-relevant flags stay")

    def test_engine_env_is_an_allow_list_of_instrument_switches(self):
        ok = dict(SPEC, arms=["rigby"], engine_env={"GGML_SCHED_PROFILE": 100})
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in ({"LD_PRELOAD": 1}, {"GGML_SCHED_PROFILE": "100"}, {"GGML_SCHED_PROFILE": True}, {},
                    {"GGML_VK_PERF_LOGGER": -1}):
            self.assertTrue(tf.spec_defects(dict(ok, engine_env=bad)), bad)
        self.assertTrue(tf.spec_defects(dict(ok, arms=["voltron"])))

    def test_the_engine_log_tail_is_raw_and_never_raises(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "llm_rigby.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("load\nSCHED PROFILE over 100 graphs, us per graph: wall 76000.0\n"
                    "  Vulkan0        splits   41.0 | wait-before       1.0 | inputs+copy   30000.0 | compute-call   5.0\n\n")
        lines = tv.log_tail_lines(p, 2)
        self.assertEqual(len(lines), 2, "the table row is kept, the blank line is not")
        self.assertTrue(lines[1].startswith("  Vulkan0"), "indentation kept - the table stays readable")
        self.assertIn("could not read", tv.log_tail_lines(p + ".missing", 5)[0])

    # Fixture mirrors the `lspci`-style driver files (Model, IRQ, GPU UUID, Bus Location) that tablet-foundry parses.
    # The IRQ and Bus Location lines are load-bearing: omitting them changes the UUID regex's capture group.
    GTX = ("Model: \t\t NVIDIA GeForce GTX 1060 6GB\nIRQ: \t\t 31\n"
           "GPU UUID: \t GPU-fea0d3ed-8dd2-64d1-aa3d-8ff72ff41874\nBus Location: \t 0000:01:00.0\n")
    GT = ("Model: \t\t NVIDIA GeForce GT 730\nIRQ: \t\t 32\n"
          "GPU UUID: \t GPU-6dae2e65-805f-1f1d-bc3f-814ef8a5e788\nBus Location: \t 0000:02:00.0\n")

    def test_cuda_sees_only_the_named_card_by_uuid(self):
        both = self.GTX + self.GT
        self.assertEqual(tv.gpu_inventory(both), [("NVIDIA GeForce GTX 1060 6GB", "GPU-fea0d3ed-8dd2-64d1-aa3d-8ff72ff41874"),
                                                  ("NVIDIA GeForce GT 730", "GPU-6dae2e65-805f-1f1d-bc3f-814ef8a5e788")])
        self.assertEqual(tv.cuda_visible("gtx1060", both), "GPU-fea0d3ed-8dd2-64d1-aa3d-8ff72ff41874")
        self.assertEqual(tv.cuda_visible("gt730", both), "GPU-6dae2e65-805f-1f1d-bc3f-814ef8a5e788")
        with self.assertRaises(RuntimeError):
            tv.cuda_visible("gtx1060", self.GT)

    def test_a_cuda_device_is_picked_by_name_like_a_vulkan_one(self):
        text = "Available devices:\n  CUDA0: NVIDIA GeForce GTX 1060 6GB (6064 MiB, 5980 MiB free)\n"
        names = tv._DEV_LINE.findall(text)
        self.assertEqual(tv.pick_vulkan(names, "gtx1060"), "CUDA0")

    def test_the_cuda_build_runs_from_its_own_mount_under_the_host_loader(self):
        self.assertEqual(tv.card_dir("cuda"), tv.LLAMA_CUDA)
        self.assertEqual(tv.card_dir("next"), tv.next_dirs()["vulkan"])
        self.assertEqual(tv.card_dir(None), tv.LLAMA_VULKAN)
        with mock.patch.object(tv, "cuda_visible", return_value="GPU-x"):
            head, env = tv.card_launch(tv.LLAMA_CUDA, "llama-server", "gtx1060")
        self.assertEqual(head[-1], os.path.join(tv.LLAMA_CUDA, "llama-server"))
        self.assertIn(tv.LLAMA_CUDA, head[2])
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "GPU-x")
        self.assertIsNone(env.get("GGML_VK_DISABLE_F16"))
        _, venv = tv.card_launch(tv.LLAMA_VULKAN, "llama-server", "gtx1060")
        self.assertNotIn("CUDA_VISIBLE_DEVICES", venv, "only the CUDA build is pinned by UUID")

    def test_build_cuda_is_the_1060s_only(self):
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="cuda")
        self.assertEqual(tf.spec_defects(ok), [])
        self.assertTrue(tf.spec_defects(dict(ok, gpu="gt730")), "no Kepler kernels in the CUDA build")
        self.assertTrue(tf.spec_defects({k: v for k, v in ok.items() if k != "fit_target"}), "needs a placement")
        self.assertTrue(tf.spec_defects(dict(ok, build="rocm")))

    def test_the_hotstore_build_is_a_cuda_build_with_its_own_mount(self):
        self.assertEqual(tv.card_dir("hotstore"), tv.LLAMA_HOTSTORE)
        self.assertNotEqual(tv.LLAMA_HOTSTORE, tv.LLAMA_CUDA)
        with mock.patch.object(tv, "cuda_visible", return_value="GPU-x"):
            head, env = tv.card_launch(tv.LLAMA_HOTSTORE, "llama-server", "gtx1060")
        self.assertEqual(head[-1], os.path.join(tv.LLAMA_HOTSTORE, "llama-server"))
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "GPU-x", "pinned to the 1060 like the cuda build")
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="hotstore")
        self.assertEqual(tf.spec_defects(ok), [])
        self.assertTrue(tf.spec_defects(dict(ok, gpu="gt730")), "no Kepler kernels in a CUDA build")

    def test_expert_hot_is_validated_and_reaches_the_server_argv(self):
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="hotstore",
                  expert_hot={"slots": -1, "dwell": 1000000, "hyst": 1.3})
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in ({"slots": 0}, {"slots": -2}, {"slots": 513}, {"slots": True}, {"dwell": 5}, {"slots": 8, "x": 1},
                    {"slots": 8, "dwell": -1}, {"slots": 8, "hyst": "1.3"}, {"slots": 8, "hyst": True}, [8]):
            self.assertTrue(tf.spec_defects(dict(ok, expert_hot=bad)), bad)
        self.assertTrue(tf.spec_defects(dict(ok, build="cuda")), "only the hotstore build has the flags")
        a = tv.server_argv("/m/x.gguf", 4096, vk_dev="CUDA0", fit=("1024", 4096), vk_dir=tv.LLAMA_HOTSTORE,
                           expert_hot={"slots": 48, "dwell": 1000000, "hyst": 1.3})
        self.assertEqual(a[a.index("-ehs") + 1], "48")
        self.assertEqual(a[a.index("--expert-dwell") + 1], "1000000")
        self.assertEqual(a[a.index("--expert-hyst") + 1], "1.3")
        b = tv.server_argv("/m/x.gguf", 4096, vk_dev="CUDA0", fit=("1024", 4096), vk_dir=tv.LLAMA_HOTSTORE,
                           expert_hot={"slots": -1})
        self.assertEqual(b[b.index("-ehs") + 1], "-1")
        self.assertNotIn("--expert-dwell", b, "unset knobs keep the engine's defaults")
        self.assertNotIn("-ehs", tv.server_argv("/m/x.gguf", 4096, vk_dev="CUDA0", fit=("1024", 4096)))

    def test_moe_cache_mib_is_validated_and_reaches_the_server_argv(self):
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="cuda", moe_cache_mib=1024)
        self.assertEqual(tf.spec_defects(ok), [])
        self.assertEqual(tf.spec_defects(dict(ok, build="next")), [])
        for bad in (0, -1, 16385, True, "1024", 1024.0):
            self.assertTrue(tf.spec_defects(dict(ok, moe_cache_mib=bad)), bad)
        self.assertTrue(tf.spec_defects(dict(ok, build="hotstore")), "the hotstore build has its own, older store")
        self.assertTrue(tf.spec_defects(dict(ok, arms=["voltron"])), "a rigby-arm setting")
        bare = {k: v for k, v in ok.items() if k not in ("gpu", "fit_target")}
        self.assertTrue(tf.spec_defects(bare), "a cache of experts on the card needs a card placement")
        a = tv.server_argv("/m/x.gguf", 4096, vk_dev="CUDA0", fit=("1024", 4096), vk_dir=tv.LLAMA_CUDA,
                           moe_cache_mib=768)
        self.assertEqual(a[a.index("--moe-cache-mib") + 1], "768")
        self.assertNotIn("--moe-cache-mib", tv.server_argv("/m/x.gguf", 4096, vk_dev="CUDA0", fit=("1024", 4096)),
                         "unset keeps the engine's default (off)")
        self.assertEqual(tv.rigby_server_kwargs(ok)["moe_cache_mib"], 1024, "the production launch carries it")
        # The fixture lines exclude the cache log entry to verify `engine_report` does not fabricate data absent from the server log.
        import tempfile
        log = os.path.join(tempfile.mkdtemp(), "llm_rigby.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("0.09.073.121 W llama_model_loader: tensor overrides to CPU are used with mmap enabled - consider "
                    "using --load-mode none for better performance\n"
                    "0.10.627.405 I common_speculative_init_result: creating MTP draft context against the target "
                    "model '/models/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf'\n")
        self.assertEqual(tv.engine_report(log), [], "a launch without the cache reports nothing")
        with open(log, "a", encoding="utf-8") as f:
            f.write("llama_model_load: MoE cache size = 1023.94 MiB\n")
        self.assertEqual(tv.engine_report(log), ["llama_model_load: MoE cache size = 1023.94 MiB"])
        self.assertEqual(tv.engine_report(log + ".missing"), [], "could not read: [] - never raises")

    def test_the_hot_ranking_switch_takes_only_a_payloads_file(self):
        root = tv.ENGINE_ENV_PATH_ROOT               # the payloads folder of Tolkien's share, wherever it is mounted
        rank = root + "imatrix/a1-rigby-moe-20261014.ranking.txt"
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="hotstore", expert_hot={"slots": -1},
                  engine_env={"LAB_HOTSTORE_BLOCK": 1, "LAB_EXPERT_HOT_RANKING": rank, "LLAMA_EXPERT_HITRATE": 1})
        self.assertEqual(tf.spec_defects(ok), [])
        for bad in ("/etc/passwd", root + "../outbox/x", 1, "", root + "x y.txt", "payloads/imatrix/x.txt"):
            self.assertTrue(tf.spec_defects(dict(ok, engine_env={"LAB_EXPERT_HOT_RANKING": bad})), bad)
        self.assertTrue(tf.spec_defects(dict(ok, engine_env={"LAB_HOTSTORE_BLOCK": rank})), "a switch stays an integer")

    def test_kepler_flags_are_the_730s_only(self):
        self.assertEqual(tv.server_env(None, (1024, 4096), "gtx1060").get("GGML_VK_DISABLE_F16"), None)
        self.assertEqual(tv.server_env(None, (1024, 4096), "gt730")["GGML_VK_DISABLE_F16"], "1")
        self.assertNotIn("LD_LIBRARY_PATH", tv.server_env(None, (1024, 4096), "gtx1060"))

    def test_fit_target_and_gpu_are_validated(self):
        ok = dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="next")
        self.assertEqual(tf.spec_defects(ok), [])
        self.assertTrue(tf.spec_defects(dict(ok, gpu="gtx9090")))
        self.assertTrue(tf.spec_defects(dict(ok, fit_target=-1)))
        self.assertTrue(tf.spec_defects(dict(ok, gpu_layers=99)), "the fitter and a hand placement exclude each other")
        self.assertTrue(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu="gtx1060")), "a card needs a placement")
        self.assertTrue(tf.spec_defects(dict(ok, arms=["voltron"])))
        self.assertTrue(tf.spec_defects(dict(ok, build="live")))

    def test_placement_is_validated(self):
        self.assertEqual(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu_layers=99, override_tensor=self.OT)), [])
        self.assertEqual(tf.spec_defects(dict(SPEC, arms=["rigby"], gpu_layers=0)), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, gpu_layers=0)))                         # default arms pool the tablets
        self.assertTrue(tf.spec_defects(dict(SPEC, gpu_layers=-1)))
        self.assertTrue(tf.spec_defects(dict(SPEC, gpu_layers=True)))
        self.assertTrue(tf.spec_defects(dict(SPEC, override_tensor=self.OT)))             # no gpu_layers
        self.assertTrue(tf.spec_defects(dict(SPEC, gpu_layers=99, override_tensor=["exps"])))   # no =buffer
        self.assertTrue(tf.spec_defects(dict(SPEC, gpu_layers=99, arms=["rigby", "voltron"])))

    def test_the_placement_is_on_the_meta_and_every_row(self):
        seen = {}
        mod = fake_payload()
        with mock.patch.object(tv, "start_server", side_effect=lambda *a, **k: seen.update(k) or ("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", return_value=(GOOD, 1.0, {})):
            rows, meta = tv.run_arm("rigby", dict(SPEC, gpu_layers=99, override_tensor=self.OT), mod, "/tmp", [],
                                    lambda m: None, lambda *a: None)
        self.assertEqual((seen["gpu_layers"], seen["override_tensor"]), (99, self.OT))
        self.assertEqual((meta["gpu_layers"], meta["override_tensor"]), (99, self.OT))
        self.assertTrue(rows)
        self.assertEqual({r["gpu_layers"] for r in rows}, {99})


class RigbyCapabilities(unittest.TestCase):
    """rigby.llm / rigby.gt730 are DETECTED (what the desk places a GPU lead's pieces by), never configured."""

    def _tree(self, d, vulkan=True, hostld=True):
        for p in [os.path.join(d, "bin", "llama-server"), os.path.join(d, "models", "m.gguf")] + \
                ([os.path.join(d, "vk", "llama-server")] if vulkan else []) + \
                ([os.path.join(d, "host", "ld-linux-x86-64.so.2")] if hostld else []):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()

    def _caps(self, d, nvidia=True):
        real = os.path.exists
        with mock.patch.object(tv, "LLAMA_BIN", os.path.join(d, "bin")), \
             mock.patch.object(tv, "MODELS", os.path.join(d, "models")), \
             mock.patch.object(tv, "LLAMA_VULKAN", os.path.join(d, "vk")), \
             mock.patch.object(tv, "HOSTLIB", os.path.join(d, "host")), \
             mock.patch("os.path.exists", side_effect=lambda p: (nvidia if p == "/dev/nvidia0" else real(p))):
            return tv.rigby_capabilities()

    def test_both_detected_when_everything_is_there(self):
        import tempfile
        d = tempfile.mkdtemp()
        self._tree(d)
        self.assertEqual(self._caps(d), ["rigby.llm", "rigby.gt730"])

    def test_gt730_needs_the_card_the_vulkan_build_and_the_host_loader(self):
        import tempfile
        d = tempfile.mkdtemp()
        self._tree(d)
        self.assertEqual(self._caps(d, nvidia=False), ["rigby.llm"])
        d2 = tempfile.mkdtemp()
        self._tree(d2, hostld=False)
        self.assertEqual(self._caps(d2), ["rigby.llm"])

    def test_nothing_is_claimed_without_the_cpu_server(self):
        import tempfile
        self.assertEqual(self._caps(tempfile.mkdtemp()), [])

    def test_rigby_caps_are_declared_even_with_no_healthy_tablet(self):
        # placed_capabilities reads the live placement table (the gate mounts /docs): stubbed so the test pins the
        # rule, not the mount - and a placed piece can still go to rigby while every tablet is down
        with mock.patch.object(tv, "rigby_capabilities", return_value=["rigby.llm"]), \
                mock.patch.object(tv, "placed_capabilities", return_value=["fellowship.placed"]):
            caps, ok = tf.detect_capabilities(health={"frodo": {"healthy": False}})
        self.assertEqual((caps, ok), (["rigby.llm", "fellowship.placed"], []))


class ReasoningSwitch(unittest.TestCase):
    def test_thinking_off_reaches_the_server_command(self):
        a = tv.server_argv("/models/qwen36.gguf", 2048, reasoning="off")
        self.assertEqual(a[a.index("--reasoning") + 1], "off")
        self.assertNotIn("--reasoning", tv.server_argv("/models/qwen36.gguf", 2048))

    def test_reasoning_is_validated(self):
        self.assertEqual(tf.spec_defects(dict(SPEC, reasoning="off")), [])
        self.assertTrue(tf.spec_defects(dict(SPEC, reasoning="maybe")))


class ItemSubset(unittest.TestCase):
    SPEC3 = dict(SPEC, tasks=["add", "sub"], replicates=3)

    def test_items_names_exactly_the_pairs_to_run(self):
        s = dict(self.SPEC3, items=[["sub", 2], ["add", 3]])
        self.assertEqual(tf.spec_defects(s), [])
        self.assertEqual(tv.items_for(s), [("sub", 2), ("add", 3)])
        self.assertEqual(len(tv.items_for(self.SPEC3)), 6)

    def test_items_outside_tasks_or_replicates_are_defects(self):
        for bad in ([["mul", 1]], [["add", 4]], [["add", 0]], [["add"]], [], "add"):
            self.assertTrue(tf.spec_defects(dict(self.SPEC3, items=bad)), bad)


class EngineHung(FreshOrder):
    """A silent answer + a silent 4-token probe = a hung llama-server: the order stops with its checkpoint (held for a
    requeue on a fresh server) and the silent answer is not recorded - never an error row per silent answer."""

    def _run(self, alive):
        asked = []

        def answer(p, s, t, **kw):
            asked.append((p.split("\n")[0], s))
            if p.startswith("write add") and s == 1:
                raise TimeoutError("timed out")
            return GOOD, 1.0, {}
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), mock.patch.object(tv, "complete", side_effect=answer), \
             mock.patch.object(tv, "engine_alive", return_value=alive):
            rows, meta = tv.run_arm("rigby", dict(SPEC), fake_payload(), "/tmp", [], lambda m: None, lambda *a: None)
        return rows, meta, asked

    def test_a_hung_server_stops_the_order_and_keeps_the_silent_answer_unrecorded(self):
        rows, meta, asked = self._run(alive=False)
        self.assertTrue(meta.get("stopped"))
        self.assertIn("hung", meta.get("engine_hung", ""))
        self.assertEqual([(r["task"], r["replicate"]) for r in rows], [("control_add", 1)],
                         "only the control was answered; the silent add r1 must not be a row (a requeue re-asks it)")
        self.assertEqual(len(asked), 2, "nothing is asked after the server is known hung")

    def test_a_silent_answer_on_a_live_server_is_an_error_row_and_the_order_goes_on(self):
        rows, meta, asked = self._run(alive=True)
        self.assertFalse(meta.get("stopped"))
        by = {(r["task"], r["replicate"]): r for r in rows}
        self.assertIn("TimeoutError", by[("add", 1)]["error"])
        self.assertTrue(by[("add", 2)]["ran"])
        self.assertFalse(by[("add", 1)].get("priced_out"), "silence is never a price")


class PriceCeiling(FreshOrder):
    def test_a_ceiling_is_a_positive_number_of_seconds(self):
        self.assertEqual(tf.spec_defects(dict(SPEC, price_ceiling_s=1830)), [])
        self.assertEqual(tf.spec_defects(dict(SPEC, price_ceiling_s=45.5)), [])
        for bad in (0, -5, "10x", True, [60]):
            self.assertTrue(tf.spec_defects(dict(SPEC, price_ceiling_s=bad)), bad)

    def test_the_ceiling_reaches_every_graded_answer_but_never_the_control(self):
        seen = []
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", side_effect=lambda p, s, t, **kw: seen.append(kw["ceiling_s"])
                               or (GOOD, 1.0, {})):
            rows, _ = tv.run_arm("rigby", dict(SPEC, price_ceiling_s=1830), fake_payload(), "/tmp", [],
                                 lambda m: None, lambda *a: None)
        self.assertEqual([r["task"] for r in rows][0], "control_add")
        self.assertEqual(seen, [None, 1830, 1830])


class ModelsManifest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self.models = os.path.join(self.d, "models")
        os.mkdir(self.models)
        for f in ("Qwen2.5-7B-Instruct-Q4_K_M.gguf", "mmproj-Qwen2.5-VL.gguf", "README.md", "a-0.5B.gguf"):
            open(os.path.join(self.models, f), "w").close()
        self.out = os.path.join(self.d, "rigby-models.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_it_publishes_the_model_ggufs_only(self):
        self.assertEqual(tv.publish_models(self.out, self.models),
                         ["Qwen2.5-7B-Instruct-Q4_K_M.gguf", "a-0.5B.gguf"])
        import json
        self.assertEqual(json.load(open(self.out))["models"], ["Qwen2.5-7B-Instruct-Q4_K_M.gguf", "a-0.5B.gguf"])

    def test_an_unchanged_set_is_not_rewritten_and_a_new_model_is(self):
        tv.publish_models(self.out, self.models)
        with mock.patch.object(tv.os, "replace", wraps=os.replace) as rep:
            tv.publish_models(self.out, self.models)
            self.assertEqual(rep.call_count, 0)
            open(os.path.join(self.models, "new-14B.gguf"), "w").close()
            self.assertIn("new-14B.gguf", tv.publish_models(self.out, self.models))
            self.assertEqual(rep.call_count, 1)

    def test_a_missing_models_dir_reports_none_and_never_raises(self):
        self.assertIsNone(tv.publish_models(self.out, os.path.join(self.d, "absent")))
        self.assertFalse(os.path.exists(self.out))


_TOOL_ROOTS = (r"E:\SSDShared\tabfoundry-jobs\payloads\map_tool_swarm_hard.py",
               "/ssdshared/tabfoundry-jobs/payloads/map_tool_swarm_hard.py")


def _tool_payload():
    """The REAL tool payload the fleet's tool cells grade with; absent is a failure, never a skip."""
    for p in _TOOL_ROOTS:
        if os.path.exists(p):
            return tv.load_scorer(p)[0]
    raise AssertionError("tool payload not reachable at %s" % (_TOOL_ROOTS,))


class ToolCells(FreshOrder):
    def setUp(self):
        super().setUp()
        self.mod = _tool_payload()

    def gold(self, task):
        t = self.mod.TASKS[task]
        import json
        return json.dumps({"tool": t["gold_tool"], "arguments": t["gold_args"] or {}})

    def test_the_tool_payload_is_recognised_and_its_control_is_its_own(self):
        self.assertTrue(tv.is_tool(self.mod))
        self.assertFalse(tv.is_tool(fake_payload()))
        self.assertEqual(tv.control_of(self.mod)[0], "control_tool")
        self.assertEqual(tv.control_of(fake_payload())[0], "control_add")

    def test_the_prompt_is_the_payloads_preamble_around_the_request(self):
        task = sorted(self.mod.TASKS)[0]
        p = tv.prompt_of(self.mod, self.mod.TASKS[task])
        self.assertIn(self.mod.TASKS[task]["req"], p)
        self.assertTrue(p.startswith("You are a tool-using agent"))

    def test_the_gold_call_passes_and_a_wrong_tool_fails(self):
        graded = 0
        for task, t in self.mod.TASKS.items():
            self.assertTrue(tv.grade(self.mod, None, t, self.gold(task))["ok"], task)
            wrong = "none" if t["gold_tool"] != "none" else "restart_service"
            import json
            bad = tv.grade(self.mod, None, t, json.dumps({"tool": wrong, "arguments": {}}))
            self.assertFalse(bad["ok"], task)
            self.assertEqual(bad["checks_total"], tv.n_checks(t))
            graded += 1
        self.assertGreater(graded, 0)

    def test_a_tool_arm_sends_the_payloads_grammar_and_grades_its_control(self):
        seen = []
        task = sorted(self.mod.TASKS)[0]
        ctl = self.mod.CONTROL_TASKS["control_tool"]
        import json
        ctl_gold = json.dumps({"tool": ctl["gold_tool"], "arguments": ctl["gold_args"]})

        def answer(prompt, seed, temp, **kw):
            seen.append(kw.get("grammar"))
            return (ctl_gold if ctl["req"] in prompt else self.gold(task)), 1.0, {}
        spec = dict(SPEC, tasks=[task], replicates=1)
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), mock.patch.object(tv, "complete", side_effect=answer):
            rows, _ = tv.run_arm("rigby", spec, self.mod, None, [], lambda m: None, lambda *a: None)
        self.assertEqual([r["task"] for r in rows], ["control_tool", task])
        self.assertTrue(all(r["ok"] for r in rows))
        self.assertTrue(seen and all(g == self.mod.GBNF and g for g in seen))

    def test_an_answer_filed_under_thinking_is_graded_and_says_so(self):
        task = sorted(self.mod.TASKS)[0]
        ctl = self.mod.CONTROL_TASKS["control_tool"]
        import json
        ctl_gold = json.dumps({"tool": ctl["gold_tool"], "arguments": ctl["gold_args"]})

        def answer(prompt, seed, temp, **kw):
            return "", 1.0, {"thinking": ctl_gold if ctl["req"] in prompt else self.gold(task)}
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), mock.patch.object(tv, "complete", side_effect=answer):
            rows, _ = tv.run_arm("rigby", dict(SPEC, tasks=[task], replicates=1), self.mod, None, [],
                                 lambda m: None, lambda *a: None)
        self.assertEqual([(r["ok"], r["graded_field"]) for r in rows], [(True, "thinking"), (True, "thinking")])

    def test_a_code_arm_sends_no_grammar(self):
        seen = []
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "complete", side_effect=lambda p, s, t, **kw: seen.append(kw.get("grammar"))
                               or (GOOD, 1.0, {})):
            tv.run_arm("rigby", SPEC, fake_payload(), "/tmp", [], lambda m: None, lambda *a: None)
        self.assertEqual(set(seen), {None})


class StreamedAnswer(unittest.TestCase):
    """complete() ends an answer on SILENCE, never on elapsed time: a real local server streams SSE chunks."""

    def _serve(self, chunks, gap):
        import http.server
        import json as _json
        import threading
        import time as _time

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for c in chunks:
                        _time.sleep(gap)
                        self.wfile.write(("data: %s\n\n" % _json.dumps(c)).encode("utf-8"))
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    CHUNKS = [{"choices": [{"delta": {"content": "def add(a, b):"}}]},
              {"choices": [{"delta": {"content": "\n    return a + b"}}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}],
               "timings": {"prompt_n": 12, "predicted_n": 9, "prompt_per_second": 40.0, "predicted_per_second": 6.0}}]

    def test_an_answer_longer_than_the_idle_window_still_completes(self):
        port = self._serve(self.CHUNKS, gap=0.3)           # 0.9 s in total, never 0.5 s of silence
        with mock.patch.object(tv, "SERVER_PORT", port), mock.patch.object(tv, "IDLE_S", 0.5):
            text, wall, meta = tv.complete("write add", 1, 0.6)
        self.assertEqual(text, "def add(a, b):\n    return a + b")
        self.assertGreater(wall, 0.5)
        self.assertEqual((meta["gen_tokens"], meta["gen_tps"], meta["finish_reason"]), (9, 6.0, "stop"))

    TOOL_CHUNKS = [{"choices": [{"delta": {"reasoning_content": "need add"}}]},
                   {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "type": "function",
                                                           "function": {"name": "add", "arguments": "{\"a\": 2,"}}]}}]},
                   {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"b\": 3}"}}]}}]},
                   {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "timings": {"prompt_n": 30, "predicted_n": 12, "prompt_ms": 1500.5, "predicted_ms": 2400.0,
                                "cache_n": 900}}]

    def test_chat_carries_the_servers_read_and_write_times_and_cached_tokens(self):
        """Per-call telemetry: a read-time share of wall needs the server's own clocks, and the cached count is the
        other half of 'novel tokens' (prompt_n is only what was processed)."""
        port = self._serve(self.TOOL_CHUNKS, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            r = tv.chat([{"role": "user", "content": "add 2 and 3"}], tools=[{"type": "function", "function": {
                "name": "add", "parameters": {"type": "object"}}}], options={"temperature": 0.6})
        self.assertEqual((r["prompt_eval_duration"], r["eval_duration"], r["cache_n"]), (1500500000, 2400000000, 900))

    def test_chat_without_server_timings_reports_none_not_zero(self):
        bare = [{"choices": [{"delta": {"content": "x"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        port = self._serve(bare, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            r = tv.chat([{"role": "user", "content": "hi"}])
        self.assertEqual((r["prompt_eval_duration"], r["eval_duration"], r["cache_n"]), (None, None, None))

    def test_chat_assembles_streamed_tool_calls_in_the_foundry_shape(self):
        """chat() returns infer_ollama._chat's shape, so the A1 lab provider maps both factories one way."""
        port = self._serve(self.TOOL_CHUNKS, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            r = tv.chat([{"role": "user", "content": "add 2 and 3"}], tools=[{"type": "function", "function": {
                "name": "add", "parameters": {"type": "object"}}}], options={"temperature": 0.6})
        self.assertEqual(r["message"]["tool_calls"], [{"function": {"name": "add", "arguments": {"a": 2, "b": 3}}}])
        self.assertEqual(r["message"]["thinking"], "need add")
        self.assertEqual((r["prompt_eval_count"], r["eval_count"], r["done_reason"]), (30, 12, "tool_calls"))

    def test_chat_passes_a_malformed_tool_call_back_as_text(self):
        bad = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "add",
                                                                                  "arguments": "{\"a\": 2"}}]}}]},
               {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
        port = self._serve(bad, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            r = tv.chat([{"role": "user", "content": "x"}])
        self.assertEqual(r["message"]["tool_calls"][0]["function"]["arguments"], "{\"a\": 2")

    def test_chat_is_ended_by_silence_not_by_length(self):
        port = self._serve(self.CHUNKS, gap=0.3)           # 0.9 s in total, never 0.5 s of silence
        with mock.patch.object(tv, "SERVER_PORT", port), mock.patch.object(tv, "IDLE_S", 0.5):
            r = tv.chat([{"role": "user", "content": "write add"}])
        self.assertEqual(r["message"]["content"], "def add(a, b):\n    return a + b")

    def test_chat_sends_the_runaway_guards_cap_as_max_tokens_and_nothing_else_caps(self):
        """Maps the order's turn_cap to max_tokens to prevent requests for the entire context (max_tokens: -1)."""
        import io
        sent = []

        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            return io.BytesIO(b'data: {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}\n'
                              b"data: [DONE]\n")
        with mock.patch.object(tv.urllib.request, "urlopen", urlopen):
            tv.chat([{"role": "user", "content": "x"}], options={"temperature": 0.6, "num_predict": 20480})
            tv.chat([{"role": "user", "content": "x"}], options={"temperature": 0.6})
        self.assertEqual([b["max_tokens"] for b in sent], [20480, -1])
        self.assertNotIn("num_predict", sent[0], "llama-server's name is max_tokens")

    def test_chat_reports_a_capped_answer_as_length(self):
        port = self._serve([{"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]}], gap=0.01)
        with mock.patch.object(tv, "SERVER_PORT", port):
            self.assertEqual(tv.chat([{"role": "user", "content": "x"}])["done_reason"], "length")

    @staticmethod
    def _fx():
        import json as _json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop_fixtures.json"), encoding="utf-8") as f:
            return _json.load(f)

    def _sse(self, text, final="stop"):
        chunks = [{"choices": [{"delta": {"content": text[i:i + 50]}}]} for i in range(0, len(text), 50)]
        return chunks + [{"choices": [{"delta": {}, "finish_reason": final}]}]

    def test_the_loop_rule_on_real_answers(self):
        """Validates loop detection against edge cases where standard heuristics fail:
        high-scoring repetitive loops, low-scoring legitimate finishes, and single-window dips.
        Ensures the detector catches true loops without falsely flagging valid content."""
        fx = self._fx()
        self.assertTrue(tv.looping(fx["loop"]["text"]))
        self.assertFalse(tv.looping(fx["legit_lowest"]["text"]), "the lowest genuine finish must never be cut")
        self.assertFalse(tv.looping(fx["correct_single_dip"]["text"]), "one repetitive window is not a loop")
        self.assertTrue(tv.looping(fx["counting_loop"]["text"]), "a counting loop is a loop")

    def test_a_looping_answer_ends_as_loop_before_the_stream_does(self):
        loop = self._fx()["loop"]["text"] * 3
        port = self._serve(self._sse(loop, final="length"), gap=0)
        with mock.patch.object(tv, "SERVER_PORT", port):
            text, wall, meta = tv.complete("write add", 1, 0.6)
        self.assertEqual(meta["finish_reason"], "loop")
        self.assertLess(len(text), len(loop), "the stream must be left early, not read to the context")
        self.assertTrue(tv.at_cap(meta), "a loop counts as an answer at the cap for the brake")

    def test_a_genuine_long_answer_streams_to_its_own_end(self):
        legit = self._fx()["legit_lowest"]["text"]
        port = self._serve(self._sse(legit), gap=0)
        with mock.patch.object(tv, "SERVER_PORT", port):
            text, wall, meta = tv.complete("write add", 1, 0.6)
        self.assertEqual((text, meta["finish_reason"]), (legit, "stop"))

    def test_silence_longer_than_the_idle_window_fails(self):
        port = self._serve(self.CHUNKS, gap=1.0)
        with mock.patch.object(tv, "SERVER_PORT", port), mock.patch.object(tv, "IDLE_S", 0.3):
            with self.assertRaises(OSError):
                tv.complete("write add", 1, 0.6)

    LONG = [{"choices": [{"delta": {"content": "tok "}}]}] * 60

    def test_a_stop_slip_cuts_the_answer_in_flight(self):
        import threading
        port = self._serve(self.LONG, gap=0.05)             # 3 s of answer
        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        with mock.patch.object(tv, "SERVER_PORT", port):
            text, wall, meta = tv.complete("write add", 1, 0.6, stop_event=stop)
        self.assertEqual(meta["finish_reason"], "stopped")
        self.assertLess(wall, 2.0, "a stop must not wait for the answer to end")
        self.assertTrue(text.startswith("tok "), "the partial answer is kept")
        self.assertLess(len(text), len("tok ") * 60)
        self.assertGreater(meta["gen_tokens"], 0)

    def test_the_price_ceiling_cuts_the_answer_and_names_it(self):
        port = self._serve(self.LONG, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            text, wall, meta = tv.complete("write add", 1, 0.6, ceiling_s=0.5)
        self.assertEqual(meta["finish_reason"], "priced_out")
        self.assertLess(wall, 2.0)

    def test_no_stop_and_no_ceiling_is_the_whole_answer(self):
        import threading
        port = self._serve(self.CHUNKS, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port):
            text, _, meta = tv.complete("write add", 1, 0.6, stop_event=threading.Event(), ceiling_s=60)
        self.assertEqual((text, meta["finish_reason"]), ("def add(a, b):\n    return a + b", "stop"))

    def test_the_thinking_is_kept_and_the_live_view_sees_it(self):
        chunks = [{"choices": [{"delta": {"reasoning_content": "hmm, "}}]},
                  {"choices": [{"delta": {"reasoning_content": "add them"}}]},
                  {"choices": [{"delta": {"content": "def add(a, b): return a + b"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        seen = []
        port = self._serve(chunks, gap=0.05)
        with mock.patch.object(tv, "SERVER_PORT", port), mock.patch.object(tv, "LIVE_EVERY_S", 0):
            text, _, meta = tv.complete("write add", 1, 0.6, live=lambda a, t, n, s: seen.append((a, t, n)))
        self.assertEqual(meta["thinking"], "hmm, add them")
        self.assertEqual(text, "def add(a, b): return a + b")
        self.assertIn(("", "hmm, add them", 2), seen)


class AtCapBrake(FreshOrder):
    """An order stating its family's at-cap share stops the arm mid-order once the evidence says the share is reached."""
    LOOP = ("loop " * 40, 50.0, {"finish_reason": "length", "prompt_tokens": 20, "gen_tokens": 80})
    FINE = (GOOD, 2.0, {"finish_reason": "stop", "prompt_tokens": 20, "gen_tokens": 10})

    def drive(self, answers, **extra):
        spec = dict(SPEC, replicates=5, ctx=100, **extra)
        logs = []
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.5)), \
                mock.patch.object(tv, "stop_server"), \
                mock.patch.object(tv, "complete", side_effect=list(answers)):
            rows, meta = tv.run_arm("rigby", spec, fake_payload(), "/tmp", [], logs.append, lambda *a: None)
        return rows, meta, logs

    def test_two_answers_at_the_context_stop_the_arm(self):
        rows, meta, logs = self.drive([self.FINE] + [self.LOOP] * 5, at_cap_brake=0.25)
        self.assertEqual(len(rows), 3, "control + the two answers that proved the share; nothing after")
        self.assertEqual((meta["braked"]["at_cap"], meta["braked"]["answers"]), (2, 2))
        self.assertTrue(any("AT-CAP BRAKE" in m for m in logs))
        self.assertNotIn("stopped", meta, "a braked arm is finished, not held for a resume")

    def test_positive_control_without_the_field_every_answer_runs(self):
        rows, meta, _ = self.drive([self.FINE] + [self.LOOP] * 5)
        self.assertEqual(len(rows), 6)
        self.assertNotIn("braked", meta)

    def test_one_runaway_among_good_answers_does_not_stop_it(self):
        rows, meta, _ = self.drive([self.FINE, self.LOOP] + [self.FINE] * 4, at_cap_brake=0.25)
        self.assertEqual(len(rows), 6)
        self.assertNotIn("braked", meta)

    def test_live_row_shapes_count_cached_prompt_and_server_stopping_short(self):
        live = [("loop", 3984.0, {"finish_reason": "length", "prompt_tokens": 184, "gen_tokens": 32559}),
                ("loop", 3987.0, {"finish_reason": "length", "prompt_tokens": 1, "gen_tokens": 32559})]
        rows, meta, _ = self.drive([self.FINE] + live + [self.FINE] * 3, at_cap_brake=0.25)
        self.assertEqual(len(rows), 3)
        self.assertEqual(meta["braked"]["at_cap"], 2)

    def test_an_answer_the_grader_could_not_run_still_counts(self):
        with mock.patch.object(tv, "grade", side_effect=lambda mod, work, tdef, ans: (
                (_ for _ in ()).throw(OSError(7, "Argument list too long")) if ans.startswith("loop") else
                {"ok": True, "checks_passed": 1, "checks_total": 1, "error": ""})):
            rows, meta, _ = self.drive([self.FINE] + [self.LOOP] * 5, at_cap_brake=0.25)
        self.assertEqual(len(rows), 3, "ungraded answers at the cap are still evidence")
        self.assertIn("Argument list too long", rows[1]["error"])
        self.assertEqual(meta["braked"]["at_cap"], 2)

    def test_the_control_never_counts(self):
        rows, meta, _ = self.drive([self.LOOP, self.LOOP] + [self.FINE] * 4, at_cap_brake=0.25)
        self.assertEqual(len(rows), 6, "one answer at the cap plus a capped control is not two")
        self.assertNotIn("braked", meta)

    def test_the_share_is_validated(self):
        self.assertEqual(tf.spec_defects(dict(SPEC, at_cap_brake=0.25)), [])
        for bad in (0, 1.5, True, "0.25"):
            self.assertTrue(tf.spec_defects(dict(SPEC, at_cap_brake=bad)), bad)


class RunArm(FreshOrder):
    def setUp(self):
        super().setUp()
        self.mod = fake_payload()
        self.stopped = []
        self.killed = []
        self.answers = {"control add\nReturn only code.": GOOD, "write add\nReturn only code.": GOOD}
        self.patches = [
            mock.patch.object(tv, "start_server", return_value=("proc", 1.5)),
            mock.patch.object(tv, "stop_server", side_effect=lambda p: self.stopped.append(p)),
            mock.patch.object(tv, "complete", side_effect=lambda prompt, seed, t, **kw: (
                self.answers[prompt], 2.0, {"gen_tokens": 10, "gen_tps": 5.0})),
            mock.patch.object(tv, "start_backends", side_effect=lambda hosts, log: list(hosts)),
            mock.patch.object(tv, "kill_backends", side_effect=lambda hosts, adb: self.killed.append(list(hosts)) or []),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def arm(self, arm, hosts=("frodo", "gandalf")):
        return tv.run_arm(arm, SPEC, self.mod, "/tmp", list(hosts), lambda m: None,
                          lambda *a: None, adb_sh=lambda *a, **k: "")

    def test_rigby_answers_control_first_then_every_replicate_and_grades_them(self):
        rows, meta = self.arm("rigby")
        self.assertEqual([(r["task"], r["replicate"]) for r in rows], [("control_add", 1), ("add", 1), ("add", 2)])
        self.assertTrue(all(r["ok"] for r in rows))
        self.assertEqual({r["shape"] for r in rows}, {"rigby"})
        self.assertEqual(meta["nodes"], [])
        self.assertEqual(self.stopped, ["proc"])
        self.assertEqual(self.killed, [])                              # rigby alone never touches the tablets

    def test_a_wrong_answer_is_graded_wrong(self):
        self.answers["write add\nReturn only code."] = BAD
        rows, _ = self.arm("rigby")
        self.assertEqual([r["ok"] for r in rows], [True, False, False])

    def test_voltron_clears_orphans_first_and_cleans_up_after(self):
        rows, meta = self.arm("voltron")
        self.assertEqual(meta["nodes"], ["frodo", "gandalf"])
        self.assertEqual(len(self.killed), 2)                          # before start AND after the run
        self.assertEqual(rows[0]["shape"], "S5-voltron")
        self.assertEqual(rows[0]["nodes"], 2)

    def test_a_port_still_held_after_the_kill_refuses_to_start(self):
        tv.kill_backends.side_effect = lambda hosts, adb: self.killed.append(1) or ["gandalf"]
        rows, meta = self.arm("voltron")
        self.assertEqual(rows, [])
        self.assertIn("still held", meta["error"])
        tv.start_server.assert_not_called()

    def test_a_server_that_dies_loading_still_cleans_up_the_backends(self):
        tv.start_server.side_effect = RuntimeError("exited rc=1 while loading")
        rows, meta = self.arm("voltron")
        self.assertIn("while loading", meta["error"])
        self.assertEqual(len(self.killed), 2)

    def test_one_failed_answer_is_a_row_not_a_lost_arm(self):
        # on a LIVE server (the probe answers); a silent answer on a hung server stops the order - see EngineHung
        tv.complete.side_effect = [(GOOD, 1.0, {}), TimeoutError("read timed out"), (GOOD, 1.0, {})]
        with mock.patch.object(tv, "engine_alive", return_value=True):
            rows, meta = self.arm("rigby")
        self.assertEqual(len(rows), 3)
        self.assertFalse(rows[1]["ran"])
        self.assertIn("TimeoutError", rows[1]["error"])
        self.assertNotIn("error", meta)

    def _ckpt(self):
        import json
        p = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % SPEC["name"])
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f]
        os.remove(p)
        return rows

    def test_a_stopped_answer_is_kept_ungraded_and_ends_the_arm(self):
        self._ckpt()
        tv.complete.side_effect = [(GOOD, 1.0, {}), ("def add(a, b):\n    ret", 5.0,
                                                    {"finish_reason": "stopped", "gen_tokens": 7})]
        rows, meta = self.arm("rigby")
        self.assertEqual(len(rows), 2, "nothing is dealt after the stop")
        self.assertEqual((rows[1]["stopped"], rows[1]["ran"], rows[1]["ok"]), (True, False, False))
        self.assertNotIn("code", rows[1], "a cut answer is never graded")
        self.assertEqual(rows[1]["answer"], "def add(a, b):\n    ret")
        self.assertTrue(meta["stopped"])
        self.assertEqual(self.stopped, ["proc"], "the server is still shut down")

    def test_a_priced_out_answer_is_reported_and_the_arm_goes_on(self):
        tv.complete.side_effect = [(GOOD, 1.0, {}), ("long thinking", 900.0, {"finish_reason": "priced_out"}),
                                   (GOOD, 1.0, {})]
        rows, meta = self.arm("rigby")
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1]["priced_out"])
        self.assertFalse(rows[1]["ran"])
        self.assertIn("priced_out after 900.0s", rows[1]["error"])
        self.assertTrue(rows[2]["ok"])
        self.assertNotIn("stopped", meta)

    def test_every_row_is_checkpointed_as_it_lands(self):
        self._ckpt()
        rows, _ = self.arm("rigby")
        got = self._ckpt()
        self.assertEqual(len(got), 3)
        self.assertEqual([(r["task"], r["replicate"]) for r in got], [(r["task"], r["replicate"]) for r in rows])

    def test_the_live_file_shows_the_answer_being_written(self):
        def talk(prompt, seed, t, **kw):
            kw["live"]("def add", "thinking about add", 7, 3.0)
            return GOOD, 1.0, {}
        tv.complete.side_effect = talk
        seen = []
        tv.run_arm("rigby", SPEC, self.mod, "/tmp", [], lambda m: None, lambda *a: None,
                   streaming=lambda arm, task, rep, n: seen.append((arm, task, rep, n)))
        with open(os.path.join(tf.STATE_DIR, "llm_rigby.live.txt"), encoding="utf-8") as f:
            live = f.read()
        self.assertIn("thinking about add", live)
        self.assertIn("7 tokens", live)
        self.assertIn(("rigby", "add", 2, 7), seen)


class Order(unittest.TestCase):
    def setUp(self):
        import tablet_runner
        self.tr = tablet_runner

    def test_unknown_task_is_held_before_any_model_time(self):
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(self.tr, "hold_order") as held, \
             mock.patch.object(tv, "run_arm") as run_arm:
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, tasks=["nope"]), ["frodo"],
                                    adb_sh=lambda *a, **k: "")
        held.assert_called_once()
        run_arm.assert_not_called()

    def _dm(self, arms, legacy=False):
        spec = dict(SPEC, arms=arms, kind="voltron" if legacy else "llm")
        notes = []
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=lambda arm, *a, **k: ([{"arm": arm, "task": "add", "ok": True,
                                                                                   "ran": True}], {"arm": arm})), \
             mock.patch.object(self.tr, "retire_order", return_value="/r/v1.results.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "publish"), \
             mock.patch.object(self.tr, "notify_desk", side_effect=notes.append):
            self.tr.build_llm_order("/q/v1.tjob.json", spec, ["frodo"], adb_sh=lambda *a, **k: "")
        return notes[0]

    def _retired(self, rows):
        seen = {}
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", return_value=(rows, {"arm": "rigby"})), \
             mock.patch.object(self.tr, "retire_order", side_effect=lambda *a, **k: seen.update(k) or "/r/x.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "publish"), \
             mock.patch.object(self.tr, "notify_desk"):
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["rigby"]), [], adb_sh=lambda *a, **k: "")
        return seen

    def test_each_answer_publishes_a_rate_and_eta_the_deck_parses(self):
        row = {"arm": "rigby", "task": "add", "replicate": 1, "ok": True, "ran": True}

        def run_arm(arm, spec, mod, work, hosts, log, progress, *a, **k):
            progress(arm, 1, 3, row)
            progress(arm, 2, 3, row)
            return [row], {"arm": arm}
        clock = iter(range(1000, 100000, 60))
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=run_arm), \
             mock.patch.object(self.tr, "retire_order", return_value="/r/x.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "notify_desk"), \
             mock.patch.object(self.tr, "publish") as pub, \
             mock.patch.object(self.tr.time, "time", side_effect=lambda: next(clock)):
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["rigby"]), [], adb_sh=lambda *a, **k: "")
        etas = [c.kwargs["eta"] for c in pub.call_args_list if c.kwargs.get("state") == "running" and "eta" in c.kwargs]
        self.assertEqual(len(etas), 2)
        for eta in etas:
            self.assertRegex(eta, r"^[\d.]+ s/item · (~\d+m left|finishing)$")

    def test_a_warm_arms_progress_never_reads_more_done_than_total(self):
        # 10-15 dealearly check: the deck read "39 / 36" then "44 / 36" - warm answers a control PER NODE, so its own
        # count (and total) exceeds 1 + items and grows as nodes join; the deck must show the arm's total
        row = {"arm": "warm", "task": "add", "replicate": 1, "ok": True, "ran": True}

        def run_arm(arm, spec, mod, work, hosts, log, progress, *a, **k):
            for i, n in ((1, 3), (2, 4), (3, 4), (4, 4)):        # a second node joined after the first report
                progress(arm, i, n, row)
            return [row], {"arm": arm}
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=run_arm), \
             mock.patch.object(self.tr, "retire_order", return_value="/r/x.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "notify_desk"), \
             mock.patch.object(self.tr, "publish") as pub:
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["warm"]), ["frodo", "sam"],
                                    adb_sh=lambda *a, **k: "")
        shown = [(c.kwargs["step"], c.kwargs["total"]) for c in pub.call_args_list
                 if c.kwargs.get("state") == "running" and "step" in c.kwargs and c.kwargs["step"]]
        self.assertTrue(shown, "progress was published")
        self.assertTrue(all(s <= t for s, t in shown), shown)
        self.assertEqual(shown[-1], (4, 4))

    def test_the_control_answer_never_sets_the_lanes_pace(self):
        ctl = {"arm": "rigby", "task": "control_add", "replicate": 1, "ok": True, "ran": True}
        row = {"arm": "rigby", "task": "add", "replicate": 1, "ok": True, "ran": True}

        clock = {"t": 1000.0}                 # t0; the control lands at +172 s, the first real item at +3000 s

        def run_arm(arm, spec, mod, work, hosts, log, progress, *a, **k):
            clock["t"] = 1172.0
            progress(arm, 1, 3, ctl)
            clock["t"] = 4000.0
            progress(arm, 2, 3, row)
            return [ctl, row], {"arm": arm}
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=run_arm), \
             mock.patch.object(self.tr, "retire_order", return_value="/r/x.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "notify_desk"), \
             mock.patch.object(self.tr, "publish") as pub, \
             mock.patch.object(self.tr.time, "time", side_effect=lambda: clock["t"]):
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["rigby"]), [], adb_sh=lambda *a, **k: "")
        etas = [c.kwargs.get("eta") for c in pub.call_args_list if c.kwargs.get("state") == "running"
                and c.kwargs.get("step")]
        self.assertEqual(len(etas), 2, "one publish per answer")
        self.assertEqual(etas[0], "", "the control alone is no rate")
        self.assertRegex(etas[1], r"^[\d.]+ s/item · ")
        self.assertNotIn("172", etas[1], "the control's 172 s must not be the pace")

    def test_the_deck_shows_the_order_before_its_first_answer(self):
        # warm spends minutes starting a server on every node before any row; the tile must not read idle meanwhile
        seen = []

        def run_arm(arm, *a, **k):
            seen.extend(c.kwargs for c in pub.call_args_list)
            return [], {"arm": arm}
        with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=run_arm), \
             mock.patch.object(self.tr, "retire_order", return_value="/r/x.json"), \
             mock.patch.object(self.tr, "write_current"), mock.patch.object(self.tr, "notify_desk"), \
             mock.patch.object(self.tr, "publish") as pub:
            self.tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["warm"]), ["frodo"], adb_sh=lambda *a, **k: "")
        self.assertTrue(any(k.get("state") == "running" and k.get("name") == SPEC["name"] for k in seen), seen)

    def test_what_was_never_measured_is_named_for_the_items_rerun(self):
        rows = [{"arm": "rigby", "task": "control_add", "replicate": 1, "ok": True, "ran": True},
                {"arm": "rigby", "task": "add", "replicate": 1, "ok": False, "ran": False, "stopped": True}]
        seen = self._retired(rows)
        self.assertEqual(seen["unclaimed"], [["rigby", "add", 1], ["rigby", "add", 2]])
        self.assertFalse(seen["complete"])

    def test_a_priced_out_answer_counts_as_measured(self):
        rows = [{"arm": "rigby", "task": "control_add", "replicate": 1, "ok": True, "ran": True},
                {"arm": "rigby", "task": "add", "replicate": 1, "ok": False, "ran": False, "priced_out": True},
                {"arm": "rigby", "task": "add", "replicate": 2, "ok": True, "ran": True}]
        seen = self._retired(rows)
        self.assertEqual(seen["unclaimed"], [])
        self.assertTrue(seen["complete"])

    def test_rigby_work_is_announced_as_rigby_never_voltron(self):
        self.assertTrue(self._dm(["rigby"]).startswith("RIGBY "))
        self.assertTrue(self._dm(["local"], legacy=True).startswith("RIGBY "))
        self.assertNotIn("VOLTRON", self._dm(["rigby"]))

    def test_voltron_is_named_only_when_the_tablets_were_pooled(self):
        self.assertTrue(self._dm(["rigby", "voltron"]).startswith("RIGBY vs VOLTRON "))

    def test_a_rigby_only_order_never_asks_for_tablet_health(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_llm_rigby_only.tjob.json")
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(SPEC, arms=["rigby"]), f)
        try:
            with mock.patch.object(self.tr, "fleet_health") as health, \
                 mock.patch.object(self.tr, "build_llm_order", return_value="res") as built, \
                 mock.patch.object(self.tr, "log"):
                self.tr.build_order(path)
            health.assert_not_called()
            self.assertEqual(built.call_args[0][2], [])
        finally:
            os.remove(path)


class Resume(FreshOrder):
    """An order that runs again under its own name (a requeue, or a restart mid-order) resumes from its checkpoint:
    finished answers are kept, only missing / stopped / errored pairs and the control run (GPU infer_ollama rule)."""

    def setUp(self):
        super().setUp()
        self.mod = fake_payload()
        self.asked = []
        self.ckpt = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % SPEC["name"])

        def complete(prompt, seed, t, **kw):
            self.asked.append((prompt.split("\n")[0], seed))
            return GOOD, 2.0, {"gen_tokens": 10}
        for p in (mock.patch.object(tv, "start_server", return_value=("proc", 1.0)),
                  mock.patch.object(tv, "stop_server"),
                  mock.patch.object(tv, "complete", side_effect=complete)):
            p.start()
            self.addCleanup(p.stop)

    def write_ckpt(self, *rows):
        import json
        with open(self.ckpt, "a", encoding="utf-8") as f:
            for r in rows:
                base = {"arm": "rigby", "model": SPEC["model"], "temperature": 0.6}
                f.write(json.dumps(dict(base, **r)) + "\n")

    def arm(self, spec=SPEC, logs=None, progress=None):
        return tv.run_arm("rigby", spec, self.mod, "/tmp", [], (logs.append if logs is not None else lambda m: None),
                          progress or (lambda *a: None))

    def test_positive_control_a_fresh_order_runs_the_control_and_every_pair(self):
        rows, meta = self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 1), ("write add", 2)])
        self.assertEqual(len(rows), 3)
        self.assertNotIn("resumed", meta)
        self.assertFalse(any(r.get("resumed") for r in rows))

    def test_a_resumed_order_does_not_rerun_a_finished_answer(self):
        self.write_ckpt({"task": "control_add", "replicate": 1, "ran": True, "ok": True},
                        {"task": "add", "replicate": 1, "ran": True, "ok": True, "answer": "kept"})
        logs = []
        rows, meta = self.arm(logs=logs)
        self.assertEqual(self.asked, [("control add", 1), ("write add", 2)], "add r1 must not be asked again")
        self.assertEqual(sorted((r["task"], r["replicate"]) for r in rows),
                         [("add", 1), ("add", 2), ("control_add", 1)])
        kept = [r for r in rows if r.get("resumed")]
        self.assertEqual([(r["task"], r["replicate"], r["answer"]) for r in kept], [("add", 1, "kept")])
        self.assertEqual(meta["resumed"], 1)
        self.assertTrue(any("resuming v1 from its checkpoint - 1 finished answer(s) kept" in m for m in logs), logs)

    def test_stopped_and_errored_answers_run_again_and_priced_out_stands(self):
        self.write_ckpt({"task": "add", "replicate": 1, "ran": False, "stopped": True},
                        {"task": "add", "replicate": 2, "ran": False, "error": "TimeoutError: x"})
        self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 1), ("write add", 2)])
        os.remove(self.ckpt)
        self.asked.clear()
        self.write_ckpt({"task": "add", "replicate": 1, "ran": False, "priced_out": True})
        self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 2)])

    def test_a_later_answer_for_the_same_pair_wins_over_a_stopped_one(self):
        self.write_ckpt({"task": "add", "replicate": 1, "ran": False, "stopped": True},
                        {"task": "add", "replicate": 1, "ran": True, "ok": True})
        self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 2)])

    def test_rows_from_another_model_temperature_or_arm_are_not_kept(self):
        self.write_ckpt({"task": "add", "replicate": 1, "ran": True, "model": "other.gguf"},
                        {"task": "add", "replicate": 2, "ran": True, "temperature": 0.0},
                        {"task": "add", "replicate": 2, "ran": True, "arm": "voltron"})
        self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 1), ("write add", 2)])

    def test_the_control_always_runs_again(self):
        self.write_ckpt({"task": "control_add", "replicate": 1, "ran": True, "ok": True},
                        {"task": "add", "replicate": 1, "ran": True}, {"task": "add", "replicate": 2, "ran": True})
        rows, _ = self.arm()
        self.assertEqual(self.asked, [("control add", 1)])
        self.assertEqual(len(rows), 3)

    def test_an_unreadable_line_is_skipped_not_fatal(self):
        with open(self.ckpt, "w", encoding="utf-8") as f:
            f.write("{not json\n")
        self.write_ckpt({"task": "add", "replicate": 2, "ran": True})
        self.arm()
        self.assertEqual(self.asked, [("control add", 1), ("write add", 1)])

    def test_progress_counts_the_kept_answers(self):
        self.write_ckpt({"task": "add", "replicate": 1, "ran": True})
        seen = []
        self.arm(progress=lambda arm, i, n, row: seen.append((i, n)))
        self.assertEqual(seen, [(2, 3), (3, 3)])

    def test_a_complete_order_retires_its_checkpoint_and_an_incomplete_one_keeps_it(self):
        import tablet_runner as tr
        rows_done = [{"arm": "rigby", "task": "control_add", "replicate": 1, "ok": True, "ran": True},
                     {"arm": "rigby", "task": "add", "replicate": 1, "ok": True, "ran": True},
                     {"arm": "rigby", "task": "add", "replicate": 2, "ok": True, "ran": True}]
        for rows, kept in ((rows_done[:2], True), (rows_done, False)):
            self.write_ckpt({"task": "add", "replicate": 1, "ran": True})
            with mock.patch.object(tv, "load_scorer", return_value=(fake_payload(), "/tmp")), \
                 mock.patch.object(tv, "run_arm", return_value=(rows, {"arm": "rigby"})), \
                 mock.patch.object(tr, "retire_order", return_value="/r/x.json"), \
                 mock.patch.object(tr, "write_current"), mock.patch.object(tr, "publish"), \
                 mock.patch.object(tr, "notify_desk"), \
                 mock.patch.object(tf, "ARCHIVE_DIR", os.path.join(tf.STATE_DIR, "archive")):
                tr.build_llm_order("/q/v1.tjob.json", dict(SPEC, arms=["rigby"]), [], adb_sh=lambda *a, **k: "")
                archived = os.listdir(tf.ARCHIVE_DIR) if os.path.isdir(tf.ARCHIVE_DIR) else []
            self.assertEqual(os.path.exists(self.ckpt), kept)
            self.assertEqual(any(f.startswith("v1.checkpoint.jsonl.") for f in archived), not kept)

    def test_retire_checkpoint_with_nothing_to_retire_is_none(self):
        self.assertIsNone(tv.retire_checkpoint("never-ran"))


def fake_agentic(results, calls):
    """An agentic payload (A1): run_agentic returns scripted row fields per task and records how it was called."""
    m = types.ModuleType("fake_agentic")
    m.TASKS = {"two-fer": {"ex_dir": "/ex/two-fer"}}
    m.CONTROL_TASKS = {"control_add": {"ex_dir": "/ex/control-add"}}
    m.self_test = lambda: ""

    def run_agentic(task, tdef, **kw):
        calls.append(dict(kw, task=task))
        return dict(results[task]) if not callable(results[task]) else results[task](kw)
    m.run_agentic = run_agentic
    return m


A_SPEC = {"kind": "llm", "name": "a1-rigby", "model": "m.gguf", "payload": "/a1_payload.py", "tasks": ["two-fer"],
          "replicates": 1, "reasoning": "off", "ctx": 32768}
PASS = {"ok": True, "checks_passed": 4, "checks_total": 4, "error": None, "finish_reason": "stop", "wall_s": 9.0}


class RigbyEarlyStop(unittest.TestCase):
    """Rigby's A1 arms must use the payload's `early_stop` for the stop decision; baseline solves are supplied by Tolkien."""

    def setUp(self):
        import tempfile
        self.out = tempfile.mkdtemp(prefix="llm_outbox_")
        self.res = tempfile.mkdtemp(prefix="llm_results_")
        self.patches = [mock.patch.object(tf, "OUTBOX", self.out), mock.patch.object(tf, "RESULTS_DIR", self.res),
                        mock.patch.object(tv, "start_server", return_value=("proc", 1.0)),
                        mock.patch.object(tv, "stop_server")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _baseline(self, stamp, oks):
        rows = [{"task": "control_add", "ok": True, "ran": True}] + [
            {"task": t, "ok": ok, "ran": True} for t, ok in oks.items()]
        with open(os.path.join(self.out, "base-order.%s.results.json" % stamp), "w", encoding="utf-8") as f:
            json.dump({"name": "base-order", "rows": rows}, f)

    def _arm(self, spec, verdict_after=None):
        FreshOrder.setUp(self)          # every arm from scratch: a kept checkpoint would run nothing and pass vacuously
        seen = []
        tasks = spec["tasks"]
        results = {"control_add": PASS}
        results.update({t: dict(PASS, ok=False, checks_passed=0) for t in tasks})
        m = fake_agentic(results, [])
        m.TASKS = {t: {"ex_dir": "/ex/" + t} for t in tasks}

        def early_stop(answered, exercises, rule, base):
            seen.append((dict(answered), list(exercises), base))
            return {"why": "clearly worse"} if verdict_after and len(answered) >= verdict_after else None
        m.early_stop = early_stop
        rows, meta = tv.run_arm("rigby", dict(spec), m, None, [], lambda s: None, lambda *a: None)
        self.assertEqual([r["task"] for r in rows][:2], ["control_add", tasks[0]], "the arm really ran")
        return rows, meta, seen

    def test_the_baseline_comes_from_the_delivered_results_and_a_verdict_ends_the_arm(self):
        self._baseline("20261009-000000", {"t1": True, "t2": True, "t3": False})
        spec = dict(A_SPEC, tasks=["t1", "t2", "t3"], stop_rule={"baseline": "base-order", "margin": 2})
        rows, meta, seen = self._arm(spec, verdict_after=1)
        self.assertEqual([r["task"] for r in rows], ["control_add", "t1"], "nothing after the verdict runs")
        self.assertEqual(meta["stopped_early"], {"why": "clearly worse"})
        self.assertNotIn("stopped", meta, "an early stop is finished, not held for a resume")
        self.assertEqual(seen[-1], ({"t1": {"passed": False}}, ["t1", "t2", "t3"], 2))

    def test_no_rule_or_an_undelivered_or_incomplete_baseline_never_asks(self):
        spec = dict(A_SPEC, tasks=["t1", "t2"])
        _, meta, seen = self._arm(spec, verdict_after=1)
        self.assertEqual((seen, meta.get("stopped_early")), ([], None), "no stop_rule")
        _, meta, seen = self._arm(dict(spec, stop_rule={"baseline": "never-delivered"}), verdict_after=1)
        self.assertEqual((seen, meta.get("stopped_early")), ([], None), "baseline not delivered")
        self._baseline("20261009-000000", {"t1": True})
        _, meta, seen = self._arm(dict(spec, stop_rule={"baseline": "base-order"}), verdict_after=1)
        self.assertEqual((seen, meta.get("stopped_early")), ([], None), "baseline lacks a scored task")

    def test_a_team_arm_whose_lead_hands_nothing_stops_on_the_payloads_lead_alone(self):
        seen = []
        spec = dict(A_SPEC, tasks=["t1", "t2", "t3"], team={"helper": "fellowship", "mode": "directed"})
        FreshOrder.setUp(self)
        results = {"control_add": PASS}
        results.update({t: dict(PASS, ok=False, checks_passed=0) for t in spec["tasks"]})
        m = fake_agentic(results, [])
        m.TASKS = {t: {"ex_dir": "/ex/" + t} for t in spec["tasks"]}

        def lead_alone(ran, team):
            seen.append(([r["task"] for r in ran], team))
            return len([r for r in ran if not r["task"].startswith("control")]) >= 2
        m.lead_alone = lead_alone
        rows, meta = tv.run_arm("rigby", dict(spec), m, None, [], lambda s: None, lambda *a: None)
        self.assertEqual([r["task"] for r in rows], ["control_add", "t1", "t2"])
        self.assertIn("delegated nothing", meta["stopped_lead_alone"]["why"])
        self.assertEqual(seen[-1], (["control_add", "t1", "t2"], spec["team"]))

    def test_every_delivered_generation_of_the_baseline_merges_newest_wins(self):
        self._baseline("20261009-000000", {"t1": False, "t2": True})
        later = os.path.join(self.out, "base-order.20261009-010000.results.json")
        self._baseline("20261009-010000", {"t1": True})
        os.utime(later, (time.time() + 60, time.time() + 60))
        self.assertEqual(tv.baseline_ok(["base-order"]), {"t1": True, "t2": True})


class AgenticPayload(FreshOrder):

    def setUp(self):
        super().setUp()
        import tempfile
        self.out = tempfile.mkdtemp(prefix="llm_outbox_")
        self.res = tempfile.mkdtemp(prefix="llm_results_")
        self.patches = [mock.patch.object(tf, "OUTBOX", self.out), mock.patch.object(tf, "RESULTS_DIR", self.res),
                        mock.patch.object(tv, "start_server", return_value=("proc", 1.0)),
                        mock.patch.object(tv, "stop_server"),
                        mock.patch.object(tv, "complete", side_effect=AssertionError("complete() is not the path"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _arm(self, results, spec=None, stop_event=None):
        calls = []
        rows, meta = tv.run_arm("rigby", dict(spec or A_SPEC), fake_agentic(results, calls), None, [],
                                lambda m: None, lambda *a: None, stop_event=stop_event)
        return rows, meta, calls

    def test_control_first_then_items_with_the_orders_settings(self):
        rows, meta, calls = self._arm({"control_add": PASS, "two-fer": dict(PASS, ok=False, checks_passed=1)},
                                      spec=dict(A_SPEC, price_ceiling_s=900))
        self.assertEqual([(r["task"], r["ok"], r["ran"]) for r in rows],
                         [("control_add", True, True), ("two-fer", False, True)])
        self.assertEqual([c["task"] for c in calls], ["control_add", "two-fer"])
        self.assertEqual([c["ceiling_s"] for c in calls], [None, 900], "the control is never priced out")
        self.assertEqual({(c["ctx"], c["seed"]) for c in calls}, {(32768, 1)})
        self.assertEqual(calls[0]["log_dir"], os.path.join(self.res, "inspect", "a1-rigby"))
        self.assertNotIn("error", meta)

    def test_rigby_a1_defaults_to_the_measured_grind_ceiling_and_an_order_can_override(self):
        _, _, calls = self._arm({"control_add": PASS, "two-fer": PASS})
        self.assertEqual([c["ceiling_s"] for c in calls], [None, tv.RIGBY_A1_CEILING_S])
        self.assertEqual(tv.RIGBY_A1_CEILING_S, 10800, "measured 10-09: longest rigby pass 8,576 s; nothing past ~2.9 h")
        FreshOrder.setUp(self)          # a kept checkpoint would skip two-fer and test nothing
        _, _, calls = self._arm({"control_add": PASS, "two-fer": PASS}, spec=dict(A_SPEC, price_ceiling_s=600))
        self.assertEqual([c["task"] for c in calls], ["control_add", "two-fer"])
        self.assertEqual(calls[-1]["ceiling_s"], 600)

    def test_a_grinding_item_is_priced_out_as_not_answered_and_the_arm_goes_on(self):
        rows, meta, _ = self._arm({"control_add": PASS,
                                   "two-fer": dict(PASS, ok=False, finish_reason="priced_out", error="priced out")})
        self.assertEqual((rows[-1]["ran"], rows[-1]["ok"], rows[-1].get("priced_out")), (False, False, True))
        self.assertNotIn("stopped", meta)

    def test_an_inspect_log_ships_to_the_outbox_as_it_lands(self):
        import tempfile
        log = os.path.join(tempfile.mkdtemp(), "item.eval")
        open(log, "w").write("x")
        rows, _, _ = self._arm({"control_add": dict(PASS, inspect_log=log), "two-fer": PASS})
        self.assertTrue(rows[0]["inspect_shipped"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "inspect", "a1-rigby", "item.eval")))

    def test_a_stop_slip_cut_item_is_not_graded_and_ends_the_arm(self):
        rows, meta, _ = self._arm({"control_add": PASS,
                                   "two-fer": dict(PASS, finish_reason="stopped", error="stopped after 3s")})
        cut = rows[-1]
        self.assertEqual((cut["ran"], cut["ok"], cut.get("stopped")), (False, False, True))
        self.assertTrue(meta.get("stopped"))

    def test_a_silent_run_on_a_hung_server_holds_the_order_and_is_not_recorded(self):
        with mock.patch.object(tv, "engine_alive", return_value=False):
            rows, meta, _ = self._arm({"control_add": PASS,
                                       "two-fer": dict(PASS, ok=False, silent=True, error="TimeoutError: timed out")})
        self.assertEqual([r["task"] for r in rows], ["control_add"])
        self.assertIn("hung", meta.get("engine_hung", ""))

    def test_a_void_run_is_never_answered_and_runs_once_more_with_one_alert(self):
        """10-08: rigby's A1 rows that ended on an engine error (HTTP 400, a ToolCall crash) were recorded as ran=True
        and scored as fails. A void row (a1_validity's verdict, carried by the payload) is not an answer."""
        seq = iter([dict(PASS, ok=False, void="engine/harness error: HTTP 400"), PASS])
        sent = []
        with mock.patch.object(tf, "notify_desk", side_effect=sent.append):
            rows, meta, calls = self._arm({"control_add": PASS, "two-fer": lambda kw: next(seq)})
        self.assertEqual([(r["task"], r["ran"], r["ok"]) for r in rows],
                         [("control_add", True, True), ("two-fer", False, False), ("two-fer", True, True)])
        self.assertFalse(tv.answered(rows[1]), "a void row is not an answer, so a resume reruns it")
        self.assertEqual([c["task"] for c in calls], ["control_add", "two-fer", "two-fer"])
        self.assertEqual(len(sent), 1)
        self.assertIn("ALERT: A1 a1-rigby: two-fer VOID (engine/harness error: HTTP 400) - running it once more", sent[0])

    def test_a_second_void_stops_retrying_and_says_so(self):
        sent = []
        with mock.patch.object(tf, "notify_desk", side_effect=sent.append):
            rows, _, calls = self._arm({"control_add": PASS,
                                        "two-fer": dict(PASS, ok=False, void="a model call ran to the context")})
        self.assertEqual(len(calls), 3, "control, the void run, ONE more - never a loop")
        self.assertEqual([r["ran"] for r in rows if r["task"] == "two-fer"], [False, False])
        self.assertIn("VOID twice", sent[-1])

    def test_a_run_flag_from_the_payload_alerts_once(self):
        sent = []
        mod = fake_agentic({"control_add": PASS, "two-fer": PASS}, [])
        mod.run_flags = lambda rows, spec: ["harness changed mid-run (a, b)"] if len(rows) >= 2 else []
        with mock.patch.object(tf, "notify_desk", side_effect=sent.append):
            tv.run_arm("rigby", dict(A_SPEC, replicates=2), mod, None, [], lambda m: None, lambda *a: None)
        self.assertEqual(sum("harness changed" in s for s in sent), 1, sent)

    def test_a_silent_run_on_a_live_server_is_an_ordinary_row(self):
        with mock.patch.object(tv, "engine_alive", return_value=True):
            rows, meta, _ = self._arm({"control_add": PASS,
                                       "two-fer": dict(PASS, ok=False, silent=True, error="TimeoutError: timed out")})
        self.assertEqual([r["task"] for r in rows], ["control_add", "two-fer"])
        self.assertNotIn("silent", rows[-1], "the hint is consumed, never recorded")
        self.assertFalse(meta.get("stopped"))

    def test_reasoning_must_be_stated(self):
        rows, meta, calls = self._arm({"control_add": PASS, "two-fer": PASS}, spec=dict(A_SPEC, reasoning=None))
        self.assertEqual(calls, [])
        self.assertIn("reasoning on|off", meta.get("error", ""))
        tv.start_server.assert_not_called()

    def test_live_called_from_a_foreign_thread_keeps_the_rigby_lane(self):
        """Inspect calls the payload's live() from its own worker threads; the claim it writes must stay rigby's."""
        import threading
        lanes = {}

        def from_worker(kw):
            def call():
                lanes["bare"] = tf.lane_name()             # a foreign thread really does read as the fleet lane
                kw["live"]("", "", 3, 1.0)
            t = threading.Thread(target=call)
            t.start()
            t.join()
            return dict(PASS)
        tf.LANE.name = "rigby"
        try:
            tv.run_arm("rigby", dict(A_SPEC), fake_agentic({"control_add": from_worker, "two-fer": PASS}, []), None,
                       [], lambda m: None, lambda *a: None,
                       streaming=lambda arm, task, rep, n: lanes.setdefault("in_live", tf.lane_name()))
        finally:
            del tf.LANE.name
        self.assertEqual(lanes, {"bare": "fleet", "in_live": "rigby"})

    def test_as_lane_restores_what_was_there(self):
        self.assertEqual(tf.lane_name(), "fleet")
        with tf.as_lane("rigby"):
            self.assertEqual(tf.lane_name(), "rigby")
        self.assertEqual(tf.lane_name(), "fleet")
        self.assertFalse(hasattr(tf.LANE, "name"))

    def test_load_scorer_refuses_an_agentic_payload_whose_grader_fails_its_self_test(self):
        import tempfile
        d = tempfile.mkdtemp()
        for name, verdict in (("bad.py", "two-fer: the stub passes"), ("good.py", "")):
            open(os.path.join(d, name), "w").write(
                "TASKS = {}\nCONTROL_TASKS = {'control_add': {}}\n"
                "def self_test():\n    return %r\n"
                "def run_agentic(*a, **k):\n    return {}\n" % verdict)
        with self.assertRaisesRegex(RuntimeError, "scorer broken: two-fer: the stub passes"):
            tv.load_scorer(os.path.join(d, "bad.py"))
        mod, work = tv.load_scorer(os.path.join(d, "good.py"))
        self.assertTrue(tv.is_agentic(mod))
        self.assertIsNone(work)


class ChatBeat(unittest.TestCase):
    """chat() calls live() while chunks arrive, as complete() does: a long agentic turn keeps the order's beat."""

    def test_live_is_called_while_chunks_arrive(self):
        import io
        import json as _json
        lines = [("data: " + _json.dumps({"choices": [{"delta": {"content": "x%d" % i}}]}) + "\n").encode()
                 for i in range(3)] + [b"data: [DONE]\n"]
        seen = []
        clock = iter(range(0, 1000, 10))
        with mock.patch.object(tv.urllib.request, "urlopen", return_value=io.BytesIO(b"".join(lines))), \
             mock.patch.object(tv.time, "time", side_effect=lambda: next(clock)):
            r = tv.chat([{"role": "user", "content": "hi"}], live=lambda a, t, n, e: seen.append((a, n)))
        self.assertEqual(r["message"]["content"], "x0x1x2")
        self.assertEqual(seen, [("x0", 1), ("x0x1", 2), ("x0x1x2", 3)])


class ChatHttpError(unittest.TestCase):
    """A row that fails on `HTTP Error 400: Bad Request` must keep WHY: the server's body names the cause
    (context overflow, an unparseable message) and must reach the row."""

    def test_the_servers_reason_is_kept(self):
        import io
        import urllib.error
        body = b'{"error":{"code":400,"message":"the request exceeds the available context size"}}'
        err = urllib.error.HTTPError("http://127.0.0.1/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body))
        with mock.patch.object(tv.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                tv.chat([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertIn("exceeds the available context size", str(cm.exception))


class ChatLoopRule(unittest.TestCase):
    """chat() applies the same looping() rule as complete() over content, thinking, AND tool-call arguments; looped calls are dropped."""

    def _stream(self, deltas):
        import io
        import json as _json
        body = b"".join(("data: " + _json.dumps({"choices": [{"delta": d}]}) + "\n").encode() for d in deltas)
        with mock.patch.object(tv.urllib.request, "urlopen", return_value=io.BytesIO(body + b"data: [DONE]\n")):
            return tv.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    def test_a_looping_tool_call_is_cut_and_dropped(self):
        head = [{"tool_calls": [{"index": 0, "function": {"name": "coding_create", "arguments": "{\"content\": \""}}]}]
        body = [{"tool_calls": [{"index": 0, "function": {"arguments": "return a + b\\n"}}]}] * 2000
        r = self._stream(head + body)
        self.assertEqual(r["done_reason"], "loop")
        self.assertEqual(r["message"]["tool_calls"], [], "a call cut mid-arguments is not a call")

    def test_a_real_tool_call_is_untouched(self):
        args = "{\"path\": \"x.py\", \"content\": \"def add(a, b):\\n    return a + b\\n\"}"
        r = self._stream([{"tool_calls": [{"index": 0, "function": {"name": "coding_create", "arguments": args}}]}])
        self.assertEqual(r["done_reason"], "stop")
        self.assertEqual(r["message"]["tool_calls"][0]["function"]["name"], "coding_create")
        self.assertEqual(r["message"]["tool_calls"][0]["function"]["arguments"]["path"], "x.py")

    def test_varied_long_text_is_not_a_loop(self):
        import random
        rnd = random.Random(7)
        words = ["alpha", "branch", "cipher", "delta", "engine", "falcon", "garden", "harbor", "island", "jungle"]
        deltas = [{"content": " ".join(rnd.choice(words) + str(rnd.randint(0, 99)) for _ in range(8)) + ". "}
                  for _ in range(600)]
        self.assertNotEqual(self._stream(deltas)["done_reason"], "loop")


class RigbyReadsAnAnswerLikeTheMenu(unittest.TestCase):
    """Verify that rigby's in-order grading treats an illustrative fence before the ANSWER fence as context (not code) and rejects hedged answers, matching the menu and GPU grader behavior."""

    class _Mod:
        @staticmethod
        def extract_code(text):
            return text

        @staticmethod
        def score(code, checks, work):
            ns = {}
            try:
                exec(code, ns)
                n = sum(bool(eval(c, ns)()) for c in checks)
            except Exception as e:
                return 0, len(checks), repr(e)
            return n, len(checks), ""

    OPS = {"checks": ["lambda: tuple(ANSWER) == ('D', 'A')"]}

    def test_an_illustrative_fence_is_context_and_a_hedge_fails(self):
        right = "Rebuild:\n\n```\ndocker compose build app\n```\n\n```python\nANSWER = ('D', 'A')\n```"
        self.assertTrue(tv.grade(self._Mod, None, self.OPS, right)["ok"])
        hedge = "```python\nANSWER = ('B', 'C')\n```\n```python\nANSWER = ('D', 'A')\n```"
        self.assertFalse(tv.grade(self._Mod, None, self.OPS, hedge)["ok"])

    def test_it_is_the_same_function_the_gpu_factories_call(self):
        self.assertIs(tv.grade_jobs.answer_code, __import__("grade_jobs").answer_code)


class WarmArm(FreshOrder):
    WSPEC = dict(SPEC, model="Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", arms=["warm"])
    TABLETS_ONLY = dict(WSPEC, warm_gt730=False)      # the tablet-dealing tests: no GT 730 node

    def test_spec_rules(self):
        self.assertEqual(tv.llm_defects(self.WSPEC), [])
        self.assertTrue(tv.llm_defects(dict(self.WSPEC, model="m.gguf")), "a tablet model has no .gguf")
        self.assertTrue(tv.llm_defects(dict(self.WSPEC, arms=["warm", "rigby"])), "warm runs alone")
        self.assertTrue(tv.llm_defects(dict(self.WSPEC, gpu_layers=99)), "no rigby placement knobs")
        self.assertTrue(tv.uses_tablets(self.WSPEC) and tv.warm_only(self.WSPEC))
        self.assertEqual(tv.lane_of_spec(self.WSPEC), "fleet", "the tablet lane, beside rigby's own")

    def test_the_server_command_holds_the_tablet_model_on_the_wire_port(self):
        cmd = tv.warm_server_cmd("Qwen3.5-0.8B-Q4_K_M", 4096, "off")
        for part in ("-m Qwen3.5-0.8B-Q4_K_M.gguf", "-c 4096", "--host 0.0.0.0", "--port %d" % tv.WARM_PORT,
                     "--reasoning off", "setsid", "llama-server"):
            self.assertIn(part, cmd)

    def test_each_load_is_timed_from_its_own_launch_not_the_end_of_staging(self):
        # staging is sequential (scp per node): a node launched first must not read 0 s because the last one took a minute
        clock = [1000.0]

        # and each server is announced (on_up) the moment it answers - frodo while sam is still being staged
        ready = {}                                         # host -> clock time its server answers (45 s after launch)
        events = []

        def scp(*a, **k):
            events.append(("scp", a[0]))
            clock[0] += 30.0
            return 0, ""

        def ssh(host, cmd, timeout=None):
            if "setsid" in cmd:
                ready[host] = clock[0] + 45.0
            return 0, "HOLDS"
        with mock.patch.object(tv.tf, "_ssh", side_effect=ssh), \
             mock.patch.object(tv.tf, "_scp", side_effect=scp), \
             mock.patch.object(tv, "warm_healthy", side_effect=lambda h, timeout=5: clock[0] >= ready.get(h, 1e18)), \
             mock.patch.object(tv.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             mock.patch.object(tv.time, "time", side_effect=lambda: clock[0]):
            loads, missing = tv.start_warm(["frodo", "sam"], "m", 4096, None, lambda *a: None,
                                           on_up=lambda h: events.append(("up", h)))
        self.assertEqual(missing, [])
        self.assertEqual(loads, {"frodo": 60.0, "sam": 46.0})  # frodo seen after sam's two 30 s copies; sam polled /2 s
        self.assertLess(events.index(("up", "frodo")), events.index(("up", "sam")))
        self.assertEqual([e for e in events if e[0] == "up"], [("up", "frodo"), ("up", "sam")])

    def test_a_node_serves_while_later_nodes_are_still_coming_up(self):
        # r75 (10-15): two tablets with dead links held nine healthy nodes idle for the whole load wait
        order = []

        def fake_start(hosts, model, ctx, reasoning, log, stop_event=None, tick=None, on_up=None, extra_launched=None):
            on_up("frodo")
            for _ in range(500):                         # wait until frodo has really answered something
                if any(o[0] == "frodo" for o in order):
                    break
                time.sleep(0.01)
            order.append(("sam joins", None))
            on_up("sam")
            return {"frodo": 1.0, "sam": 9.0}, []

        def fake_complete(prompt, seed, temperature, stop_event=None, live=None, ceiling_s=None, grammar=None,
                          base=None):
            order.append(("frodo" if ("//192.0.2.2:%d" % tv.WARM_PORT) in base else "sam", seed))
            return GOOD, 0.1, {"ttft_s": 0.1}
        with mock.patch.object(tv, "start_warm", side_effect=fake_start), \
             mock.patch.object(tv, "kill_warm", return_value=[]), \
             mock.patch.object(tv, "complete", side_effect=fake_complete), \
             mock.patch.object(tv, "_wire_ip", side_effect=lambda h: {"frodo": "192.0.2.2", "sam": "192.0.2.3"}[h]):
            rows, meta = tv.run_arm("warm", self.TABLETS_ONLY, fake_payload(), "/tmp", ["frodo", "sam"], lambda m: None,
                                    lambda *a: None, adb_sh=lambda *a, **k: "")
        self.assertLess(order.index(next(o for o in order if o[0] == "frodo")), order.index(("sam joins", None)),
                        "frodo served before sam joined")
        self.assertEqual(meta["nodes"], ["frodo", "sam"])
        self.assertEqual(meta["unanswered"], 0)

    def _run(self, answers, loads=None):
        calls, killed = [], []

        def fake_complete(prompt, seed, temperature, stop_event=None, live=None, ceiling_s=None, grammar=None,
                          base=None):
            calls.append(base)
            return answers(base), 0.5, {"ttft_s": 0.2}
        loads = {"frodo": 3.0, "sam": 4.0} if loads is None else loads
        with mock.patch.object(tv, "start_warm", return_value=(loads, ["merry"])), \
             mock.patch.object(tv, "kill_warm", side_effect=lambda hosts, adb: killed.append(list(hosts)) or []), \
             mock.patch.object(tv, "complete", side_effect=fake_complete), \
             mock.patch.object(tv, "_wire_ip", side_effect=lambda h: {"frodo": "192.0.2.2", "sam": "192.0.2.3"}
                               .get(h, "192.0.2.9")):
            rows, meta = tv.run_arm("warm", self.TABLETS_ONLY, fake_payload(), "/tmp", ["frodo", "sam", "merry"],
                                    lambda m: None, lambda *a: None, adb_sh=lambda *a, **k: "")
        return rows, meta, calls, killed

    def test_each_node_answers_its_control_then_turns_spread_over_free_nodes(self):
        rows, meta, calls, killed = self._run(lambda base: GOOD)
        ctl = [r for r in rows if r["task"] == "control_add"]
        self.assertEqual(sorted(r["node"] for r in ctl), ["frodo", "sam"], "one control per warm node")
        work = [r for r in rows if r["task"] == "add"]
        self.assertEqual(sorted(r["replicate"] for r in work), [1, 2])
        self.assertTrue(all(r["ok"] and r["ttft_s"] == 0.2 and r["shape"] == "S1-warm" for r in rows))
        self.assertEqual(set(calls), {"http://192.0.2.2:%d" % tv.WARM_PORT, "http://192.0.2.3:%d" % tv.WARM_PORT})
        self.assertEqual(meta["nodes"], ["frodo", "sam"])
        self.assertEqual(meta["without_model"], ["merry"])
        self.assertEqual(killed[-1], ["frodo", "sam"], "every started server killed at the end")

    def test_a_node_that_fails_its_control_takes_no_work(self):
        rows, meta, calls, killed = self._run(lambda base: BAD if ("//192.0.2.3:%d" % tv.WARM_PORT) in base else GOOD)
        work = [r for r in rows if r["task"] == "add"]
        self.assertEqual({r["node"] for r in work}, {"frodo"})
        self.assertEqual(len(work), 2)

    def test_a_handed_back_turn_is_not_counted_and_a_long_turn_beats(self):
        dropped, progress, beats, seen = [], [], [], {}

        def fake_complete(prompt, seed, temperature, stop_event=None, live=None, ceiling_s=None, grammar=None,
                          base=None):
            seen[base] = seen.get(base, 0) + 1          # a node's first call is its control
            if seen[base] == 2 and not dropped:          # whichever node takes a turn first loses its server
                dropped.append(base)
                raise OSError("Connection refused")
            if live is not None:
                live("", "", 7, 1.0)
            return GOOD, 0.5, {"ttft_s": 0.2}
        with mock.patch.object(tv, "start_warm", return_value=({"frodo": 3.0, "sam": 4.0}, [])), \
             mock.patch.object(tv, "kill_warm", return_value=[]), \
             mock.patch.object(tv, "complete", side_effect=fake_complete), \
             mock.patch.object(tv, "_wire_ip", side_effect=lambda h: {"frodo": "192.0.2.2", "sam": "192.0.2.3"}[h]):
            rows, meta = tv.run_arm("warm", self.TABLETS_ONLY, fake_payload(), "/tmp", ["frodo", "sam"], lambda m: None,
                                    lambda arm, i, n, row: progress.append((i, n)), adb_sh=lambda *a, **k: "",
                                    streaming=lambda *a: beats.append(a))
        self.assertEqual(len(dropped), 1, "the drop must fire or this test proves nothing")
        # the total grows as nodes join (each joined node adds its control turn), so the invariant is per report
        self.assertTrue(all(i <= n for i, n in progress), progress)
        self.assertEqual(progress[-1][0], progress[-1][1], "done only when every turn is answered")
        self.assertEqual(progress[-1][1], 2 + 2, "2 work turns + 2 nodes' controls")
        self.assertEqual(sum(1 for r in rows if r["task"] == "add" and r.get("ok")), 2, "the handed-back turn answered")
        self.assertEqual(meta["unanswered"], 0)
        self.assertTrue(beats and beats[0][0] == "warm", "a generating turn beats through streaming")

    def test_servers_are_killed_even_when_nothing_came_up(self):
        rows, meta, calls, killed = self._run(lambda base: GOOD, loads={})
        self.assertIn("no warm server came up", meta["error"])

    def test_turn_cap_is_refused_at_the_door_unless_it_leaves_room_in_the_context(self):
        """Per-call cap must leave room for the reply; compaction ensures this, so values >= ctx or invalid are refused here."""
        base = dict(SPEC, arms=["rigby"], ctx=65536)
        self.assertEqual(tv.llm_defects(dict(base, turn_cap=20480)), [])
        for bad in (65536, 70000, 0, -1, "20480", True):
            self.assertTrue(tv.llm_defects(dict(base, turn_cap=bad)), bad)

    def test_warm_gt730_is_a_warm_order_flag_only(self):
        self.assertEqual(tv.llm_defects(dict(self.WSPEC, warm_gt730=True)), [])
        self.assertEqual(tv.llm_defects(dict(self.WSPEC, warm_gt730=False)), [], "the opt-out")
        self.assertTrue(tv.llm_defects(dict(self.WSPEC, warm_gt730="yes")))
        self.assertTrue(tv.llm_defects(dict(SPEC, arms=["rigby"], warm_gt730=True)), "warm orders only")

    def test_which_orders_touch_the_730(self):
        u = tv.spec_uses_gt730
        self.assertFalse(u(dict(SPEC, arms=["rigby"])), "CPU-only rigby order")
        self.assertTrue(u(dict(SPEC, arms=["rigby"], gpu_layers=99)), "a hand placement defaults to the 730")
        self.assertTrue(u(dict(SPEC, arms=["rigby"], gpu="gt730", fit_target=256)))
        self.assertFalse(u(dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="cuda")), "1060 only")
        self.assertFalse(u(dict(SPEC, arms=["rigby"], gpu="gtx1060", fit_target=1024, build="next")))
        self.assertTrue(u(dict(SPEC, arms=["rigby"], gpu=["gtx1060", "gt730"], fit_target=[1024, 256])))
        self.assertTrue(u(dict(SPEC, arms=["rigby", "voltron"])), "voltron: conservative yes")
        self.assertFalse(u(self.WSPEC), "opt-in: whether warm work uses the 730 is #34 placement policy")
        self.assertTrue(u(dict(self.WSPEC, warm_gt730=True)))
        self.assertFalse(u(dict(self.WSPEC, warm_gt730=False)), "the opt-out leaves the 730 free")
        bench = {"kind": "bench", "runs": [{"id": "a", "where": "rigby", "model": "m.gguf", "p": [64], "n": [0]}]}
        self.assertFalse(u(bench), "a CPU bench run")
        self.assertTrue(u(dict(bench, runs=[dict(bench["runs"][0], gpu_layers=8)])))
        self.assertFalse(u(dict(bench, runs=[dict(bench["runs"][0], gpu="gtx1060", fit_target=1024, build="cuda")])))
        self.assertTrue(u(dict(bench, runs=[dict(bench["runs"][0], handoff=True, writer={"gpu_layers": 99})])),
                        "the handoff writer is the 730")

    def test_the_730_node_serves_locally_on_its_own_port_with_the_kepler_flags(self):
        with mock.patch.object(tv, "cuda_visible", return_value="GPU-x"):
            argv, env = tv.warm_730_argv("/models/m.gguf", 16384, "off", "Vulkan1")
        self.assertEqual(argv[argv.index("-dev") + 1], "Vulkan1")
        self.assertEqual(argv[argv.index("-ngl") + 1], "99")
        self.assertEqual(argv[argv.index("--port") + 1], str(tv.WARM_730_PORT))
        self.assertEqual(argv[argv.index("-t") + 1], str(tv.WARM_730_THREADS))
        self.assertIn(tv.LLAMA_VULKAN, argv[2], "the live Vulkan build under the host loader")
        self.assertEqual(env["GGML_VK_DISABLE_F16"], "1", "Kepler shader flags")
        self.assertNotIn("CUDA_VISIBLE_DEVICES", env)
        self.assertEqual(tv.warm_base(tv.WARM_730), "http://127.0.0.1:%d" % tv.WARM_730_PORT)

    def _run_730(self, holder=None, spec=None):
        calls, killed, stopped = [], [], []

        def fake_complete(prompt, seed, temperature, stop_event=None, live=None, ceiling_s=None, grammar=None,
                          base=None):
            calls.append(base)
            return GOOD, 0.5, {"ttft_s": 0.2}
        proc = mock.Mock()
        proc.poll.return_value = None
        if holder:
            self.assertTrue(tv.gt730_try_acquire(holder))
        try:
            def fake_start(hosts, model, ctx, reasoning, log, stop_event=None, tick=None, on_up=None,
                           extra_launched=None):
                return dict({"frodo": 3.0}, **{k: 1.0 for k in (extra_launched or {})}), []
            with mock.patch.object(tv, "start_warm", side_effect=fake_start), \
                 mock.patch.object(tv, "kill_warm", side_effect=lambda hosts, adb: killed.append(list(hosts)) or []), \
                 mock.patch.object(tv, "start_warm_730", return_value=proc) as started, \
                 mock.patch.object(tv, "stop_warm_730", side_effect=lambda p: stopped.append(p) or True), \
                 mock.patch.object(tv, "warm_healthy", return_value=True), \
                 mock.patch.object(tv, "complete", side_effect=fake_complete), \
                 mock.patch.object(tv, "_wire_ip", return_value="192.0.2.2"):
                rows, meta = tv.run_arm("warm", spec or dict(self.WSPEC, warm_gt730=True), fake_payload(), "/tmp",
                                        ["frodo"],
                                        lambda m: None, lambda *a: None, adb_sh=lambda *a, **k: "")
        finally:
            if holder:
                tv.gt730_release()
        return rows, meta, calls, killed, stopped, started

    def test_the_730_joins_as_a_warm_node_and_is_stopped_and_released(self):
        rows, meta, calls, killed, stopped, started = self._run_730()
        self.assertEqual(meta["nodes"], ["frodo", tv.WARM_730])
        self.assertIn(tv.WARM_730, {r["node"] for r in rows}, "the 730 answered turns")
        self.assertIn("http://127.0.0.1:%d" % tv.WARM_730_PORT, calls)
        self.assertNotIn(tv.WARM_730, killed[-1], "adb kill is for tablets; the 730 is stopped locally")
        self.assertEqual(len(stopped), 1)
        self.assertTrue(tv.gt730_try_acquire("next"), "the 730 claim was released")
        tv.gt730_release()

    def test_the_730_serves_only_when_the_order_asks(self):
        """Opt-in: the factory carries the capability; whether warm work uses it is #34 placement policy (the priced
        menu -> Spock's table), so neither an absent flag nor false starts it or claims the card."""
        for spec in (dict(self.WSPEC), dict(self.WSPEC, warm_gt730=False)):
            rows, meta, calls, killed, stopped, started = self._run_730(spec=spec)
            started.assert_not_called()
            self.assertEqual(meta["nodes"], ["frodo"])
            self.assertTrue(tv.gt730_try_acquire("next"), "the order never claimed the 730")
            tv.gt730_release()

    def test_a_busy_730_is_skipped_never_waited_on(self):
        rows, meta, calls, killed, stopped, started = self._run_730(holder="llm-gt730-order")
        started.assert_not_called()
        self.assertEqual(meta["gt730_busy"], "llm-gt730-order")
        self.assertEqual(meta["nodes"], ["frodo"], "the order still runs on the tablets")
        self.assertEqual(stopped, [])


if __name__ == "__main__":
    unittest.main()
