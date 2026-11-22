"""Test rigby's production launch per model. Every launch uses the model's profile; orders only change settings when explicitly named (tests). No server is started; start_server is stubbed and arguments are inspected."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rigby_profiles
import tablet_foundry as tf
import tolkien_llm as tv

A3B = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
PROF = rigby_profiles.LAUNCH[A3B]


class LaunchSpec(unittest.TestCase):
    def test_a_bare_order_launches_the_profile_and_a_named_setting_wins(self):
        got = tv.launch_spec({"model": A3B, "arms": ["rigby"]})
        self.assertEqual({k: got[k] for k in PROF}, PROF)
        self.assertEqual(tv.launch_spec({"model": A3B, "moe_cache_mib": 1024})["moe_cache_mib"], 1024)
        off = tv.launch_spec({"model": A3B, "spec_type": None, "draft_max": None})
        self.assertIsNone(off["spec_type"], "an explicit null turns MTP off - the no-MTP test arm")
        self.assertIsNone(tv.rigby_server_kwargs(off)["spec_type"])

    def test_another_card_or_a_hand_placement_takes_no_profile(self):
        for other in ({"gpu": "gt730", "gpu_layers": 10}, {"gpu_layers": 10}, {"override_tensor": ["x=CPU"]}):
            s = dict({"model": A3B}, **other)
            self.assertEqual(tv.launch_spec(s), s, other)
        s = {"model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "gpu": "gtx1060"}
        self.assertEqual(tv.launch_spec(s), s, "a model with no profile is launched as ordered")

    def test_every_profile_is_a_valid_rigby_order(self):
        for model, prof in rigby_profiles.LAUNCH.items():
            spec = dict(prof, kind="llm", name="p", model=model, payload="/p.py", tasks=["t"], arms=["rigby"])
            self.assertEqual(tf.spec_defects(spec), [], model)
        bare = {"kind": "llm", "name": "p", "model": A3B, "payload": "/p.py", "tasks": ["t"], "arms": ["rigby"],
                "moe_cache_mib": 1024}
        self.assertEqual(tf.spec_defects(bare), [], "judged as it launches: the profile supplies the build and card")


class TheWindowRunsTheProfile(unittest.TestCase):
    def test_a_chat_window_with_only_the_rows_card_fields_still_launches_mtp(self):
        """Kirk's window order carries the row's card (gpu / fit_target / build / threads / reasoning / temperature)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 10 ** 12}, f)
        spec = {"kind": "chat.window", "name": "hai-win-rigby-a3b-1", "model": A3B, "items_dir": d, "ctx": 32768,
                "idle_ttl": 300, "gpu": "gtx1060", "fit_target": 1024, "build": "cuda", "reasoning": "off"}
        self.assertEqual(tf.spec_defects(dict(spec, items_dir="/ssdshared/x")), [])
        with mock.patch.object(tf, "STATE_DIR", d), \
                mock.patch.object(tv, "start_server", return_value=("proc", 1.0)) as start, \
                mock.patch.object(tv, "stop_server"):
            summary, err = tv.run_window(spec, lambda *a: None,
                                         sleep=lambda s: open(os.path.join(d, "CLOSE"), "w").close())
        kw = start.call_args.kwargs
        for k in ("spec_type", "draft_max", "ubatch", "parallel", "op_offload_min_batch", "engine_env"):
            self.assertEqual(kw[k], PROF[k], k)
        self.assertEqual(summary["launch"]["spec_type"], "draft-mtp")


if __name__ == "__main__":
    unittest.main()
