"""Each model's context comes from ONE table (config.MODEL_NUM_CTX, the RUNBOOK residency map's tested maxima), so a
model already on a card is never reloaded at another size and a smaller model is never given a window that spills it.
The behaviour pins below read the num_ctx a real request would carry, not the source text."""
import json
import unittest
from unittest.mock import patch

import coding_review
import config


class WindowsComeFromTheMeasuredTable(unittest.TestCase):
    """HAI reads every model's window from the policy table's `contexts` (the measured fully-resident fit), not from a hand-kept copy."""

    def test_the_reader_takes_the_contexts_section_and_skips_the_source_note(self):
        import os
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "policy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"families": {}, "contexts": {"bighonker": {"qwen3.5:9b": 39936}, "source": "fit"}}, f)
        self.assertEqual(config._measured_contexts(p), {"bighonker": {"qwen3.5:9b": 39936}})

    def test_an_unreadable_table_falls_back_to_the_in_code_windows(self):
        self.assertEqual(config._measured_contexts("/no/such/policy.json"), {})

    def test_the_live_windows_are_the_measured_ones_where_the_table_is_readable(self):
        m = config.MEASURED_CONTEXTS
        if not m:
            self.skipTest("the policy table is not readable here - the in-code fallback is what runs")
        for host, box in ((config.HEAVY_HOST, "mothership"), (config.BRAIN_HOST, "bighonker"),
                          (config.FAILOVER_HOST, "softserve")):
            for model, ctx in m.get(box, {}).items():
                self.assertEqual(config.ctx_for(host, model, 1), ctx, "%s on %s" % (model, box))


class CtxFor(unittest.TestCase):
    def test_standing_models_get_their_box_setting(self):
        self.assertEqual(config.ctx_for(config.HEAVY_HOST, config.HEAVY_MODEL, 1), config.HEAVY_NUM_CTX)
        self.assertEqual(config.ctx_for(config.BRAIN_HOST, config.BRAIN_MODEL, 1), config.BRAIN_NUM_CTX)
        self.assertEqual(config.ctx_for(config.HEAVY_HOST + "/", config.HEAVY_MODEL, 1), config.HEAVY_NUM_CTX)

    def test_unmeasured_model_or_host_keeps_the_fallback(self):
        self.assertEqual(config.ctx_for(config.HEAVY_HOST, "no-such-model:1b", 16384), 16384)
        self.assertEqual(config.ctx_for("http://nowhere:11434", config.HEAVY_MODEL, 8192), 8192)

    def test_a_smaller_critic_gets_its_own_fit_not_the_heavy_window(self):
        moe = config.ctx_for(config.HEAVY_HOST, "qwen3.6:35b-a3b", 16384)
        self.assertLess(moe, config.HEAVY_NUM_CTX)
        self.assertGreater(moe, 16384)

    def test_derived_fallback_constants(self):
        self.assertEqual(config.FAILOVER_NUM_CTX,
                         config.ctx_for(config.FAILOVER_HOST, config.FAILOVER_MODEL, config.NUM_CTX))
        self.assertEqual(config.LEAD_BOX_BRAIN_NUM_CTX,
                         config.ctx_for(config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL, config.BRAIN_NUM_CTX))


class RequestsCarryTheModelsContext(unittest.TestCase):
    def test_code_review_on_the_heavy_model_uses_the_heavy_window(self):
        sent = {}

        def fake_post(url, timeout=None, json=None):
            sent.update(json)
            raise OSError("stop after capture")

        with patch("ollama_client.post_supervised", side_effect=fake_post):
            try:
                coding_review.review_model({"task": "t"}, host=config.HEAVY_HOST, model=config.HEAVY_MODEL)
            except Exception:
                pass
        self.assertEqual(sent.get("model"), config.HEAVY_MODEL)
        self.assertEqual(sent["options"]["num_ctx"], config.HEAVY_NUM_CTX)


if __name__ == "__main__":
    unittest.main()
