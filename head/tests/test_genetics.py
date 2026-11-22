"""#19 P3c/P3d — the genetics dealer's math, tier-2 VLM trait parsing, and the effort dial.

The VLM itself (qwen3-vl:8b on BigHonker) and the renderer are exercised live; these tests pin
the dealer's pure math, the fail-soft parsing contract, and the thread-local effort carrier.
"""
import json
import threading
import unittest
from unittest import mock

import genetics
import tools

TRAITS_A = {"eyes": "brown", "hair": "black", "hair_texture": "coily", "skin": "rich brown",
            "_skin_lum": 110, "lips": "full", "nose": "wide", "brows": "thick"}
TRAITS_B = {"eyes": "blue/gray", "hair": "blonde", "hair_texture": "straight", "skin": "fair",
            "_skin_lum": 200, "lips": "thin", "nose": "narrow", "brows": "thin"}


class DealerTests(unittest.TestCase):
    def test_static_path_is_fully_offline(self):
        # Normal effort = static tables (P3d): the dealer must not research
        with mock.patch.object(genetics, "researched_eye_odds",
                               side_effect=AssertionError("must not research on Normal")):
            hand, story = genetics.deal_hand(TRAITS_A, TRAITS_B, seed=5, researched=False)
        self.assertIn(hand["eyes"], genetics.EYE_COLORS)
        self.assertTrue(any(s.startswith("eyes:") for s in story))

    def test_deterministic_per_seed(self):
        h1, s1 = genetics.deal_hand(TRAITS_A, TRAITS_B, seed=9, researched=False)
        h2, s2 = genetics.deal_hand(TRAITS_A, TRAITS_B, seed=9, researched=False)
        self.assertEqual(h1, h2)
        self.assertEqual(s1, s2)

    def test_tier2_rolls_only_measured_traits(self):
        hand, story = genetics.deal_hand(
            TRAITS_A, TRAITS_B, seed=1, researched=False,
            vlm_a={"dimples": True, "cleft_chin": False}, vlm_b={"dimples": True})
        self.assertIn("dimples", hand)
        self.assertIn("cleft_chin", hand)
        for unmeasured in ("epicanthic_fold", "widows_peak", "gap_teeth"):
            self.assertNotIn(unmeasured, hand)      # unknown for BOTH parents -> silence

    def test_tier2_dominance_band(self):
        # both-parents-yes must express far more often than neither (cartoon-Mendel sanity)
        def rate(va, vb):
            hits = sum(genetics.deal_hand(TRAITS_A, TRAITS_B, seed=s, researched=False,
                                          vlm_a=va, vlm_b=vb)[0].get("dimples", False)
                       for s in range(300))
            return hits / 300
        self.assertGreater(rate({"dimples": True}, {"dimples": True}), 0.6)
        self.assertLess(rate({"dimples": False}, {"dimples": False}), 0.2)

    def test_hand_prompt_renders_rolled_tier2_only(self):
        hand = {"eyes": "brown", "hair": "black", "hair_texture": "coily", "skin": "fair",
                "sex": "girl", "dimples": True, "cleft_chin": False}
        p = genetics.hand_prompt(hand)
        self.assertIn("cheek dimples", p)
        self.assertNotIn("cleft chin", p)           # rolled-false stays unprompted
        self.assertIn("tightly coiled afro-textured hair", p)


class ColorConsensusTests(unittest.TestCase):
    def setUp(self):
        genetics._COLOR_CACHE.clear()

    def test_majority_vote_and_cache(self):
        reads = [{"eyes": "hazel/green"}, {"eyes": "brown"}, {"eyes": "hazel/green"}]
        with mock.patch.object(genetics, "_ensure_vlm_host", return_value=True), \
             mock.patch.object(genetics, "_vlm_color_read", side_effect=reads) as reader:
            out = genetics.vlm_color_traits(__file__, k=3)
            self.assertEqual(out["eyes"], "hazel/green")     # 2-of-3 wins
            self.assertEqual(reader.call_count, 3)
            genetics.vlm_color_traits(__file__, k=3)         # cached: no new reads
            self.assertEqual(reader.call_count, 3)

    def test_host_down_is_fail_soft(self):
        with mock.patch.object(genetics, "_ensure_vlm_host", return_value=False):
            self.assertEqual(genetics.vlm_color_traits(__file__), {})

    def test_measure_traits_lets_vlm_override_pixels(self):
        src = open("genetics.py", encoding="utf-8").read()
        body = src.split("def measure_traits(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("traits.update(vlm_color_traits(image_path))", body)
        self.assertIn('"_skin_lum": _skin_lum(cheek_rgb)', body)   # blend lum still seeded

    def test_boy_short_hair_prompt(self):
        hand = {"eyes": "brown", "hair": "black", "hair_texture": "wavy", "skin": "fair",
                "gender": "boy"}
        p = genetics.hand_prompt(hand, include_gender=False, short_hair=True)
        self.assertIn("short black wavy hair", p)
        p2 = genetics.hand_prompt(hand, include_gender=False)
        self.assertNotIn("short ", p2)


class _Resp:
    def __init__(self, content):
        self._c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._c}}


class VlmTraitTests(unittest.TestCase):
    def test_parses_yes_no_and_omits_unknown(self):
        with mock.patch("ollama_client.post_supervised", return_value=_Resp(json.dumps({
                "epicanthic_fold": "yes", "dimples": "No", "cleft_chin": "unknown",
                "widows_peak": "true", "gap_teeth": "n/a", "ear_protrusion": "no",
                "beauty_mark": "YES"}))):
            out = genetics.vlm_traits(__file__)     # any readable file; the post is mocked
        self.assertEqual(out["epicanthic_fold"], True)
        self.assertEqual(out["dimples"], False)
        self.assertEqual(out["widows_peak"], True)
        self.assertEqual(out["ear_protrusion"], False)
        self.assertEqual(out["beauty_mark"], True)
        self.assertNotIn("cleft_chin", out)         # unknown -> omitted, dealer stays silent
        self.assertNotIn("gap_teeth", out)          # junk answer -> omitted, not guessed

    def test_fail_soft_on_transport_error(self):
        with mock.patch("ollama_client.post_supervised", side_effect=OSError("down")):
            self.assertEqual(genetics.vlm_traits(__file__), {})

    def test_fail_soft_on_missing_file(self):
        self.assertEqual(genetics.vlm_traits("no-such-file.png"), {})

    def test_model_is_the_2026_vl_not_the_stale_one(self):
        import config
        self.assertIn("qwen3-vl", config.VLM_MODEL)
        self.assertNotIn("2.5", config.VLM_MODEL)


class RequestAxesTests(unittest.TestCase):
    """ROUTING selects the identity estimator; EFFORT sets genetics depth.
    Both use per-worker thread-local storage to avoid param threading in
    pinned chat() and run_tool() signatures."""

    def tearDown(self):
        tools.set_request_effort(None)
        tools.set_request_routing(None)

    def test_defaults_and_bind(self):
        tools.set_request_effort(None)
        tools.set_request_routing(None)
        self.assertEqual(tools.request_effort(), "normal")
        self.assertEqual(tools.request_routing(), "auto")
        tools.set_request_effort("absurd")
        tools.set_request_routing("quick_8b")
        self.assertEqual(tools.request_effort(), "absurd")
        self.assertEqual(tools.request_routing(), "quick_8b")

    def test_thread_isolation(self):
        tools.set_request_effort("absurd")
        tools.set_request_routing("quick_8b")
        seen = {}
        t = threading.Thread(target=lambda: seen.update(
            e=tools.request_effort(), r=tools.request_routing()))
        t.start()
        t.join()
        self.assertEqual(seen["e"], "normal")       # a fresh thread sees the defaults
        self.assertEqual(seen["r"], "auto")

    def test_app_binds_both_next_to_cancellation(self):
        src = open("app.py", encoding="utf-8").read()
        body = src.split("def _run_query(", 1)[1]
        self.assertIn("cancellation.bind(cid)", body)
        self.assertIn('tools.set_request_effort(getattr(q, "effort_mode", None))', body)
        self.assertIn('tools.set_request_routing(getattr(q, "routing", None))', body)

    def test_image_tools_pass_both_dials(self):
        src = open("tools.py", encoding="utf-8").read()
        for fn in ("_imagine_children", "_family_photo"):
            body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("effort=request_effort()", body, fn)
            self.assertIn("routing=request_routing()", body, fn)

    def test_routing_picks_engine_and_effort_picks_genetics(self):
        # quick_8b -> coordinate fit; auto/force_27b -> MICA (degrading neural->flame->lmk);
        # hard/absurd -> researched odds + tier-2 VLM; explicit engine= still overrides
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def simulate_children(", 1)[1]
        self.assertIn('routing != "quick_8b"', body)
        self.assertIn("want_neural and flame.neural_available()", body)
        self.assertIn("elif flame.available():", body)             # graceful degradation
        self.assertNotIn('effort == "absurd" and flame', body)     # engine left the effort axis
        self.assertIn('effort in ("think_hard", "absurd")', body)
        self.assertIn("researched=deep, vlm_a=vlm[0], vlm_b=vlm[1]", body)
        self.assertIn("if engine is None:", body)


if __name__ == "__main__":
    unittest.main()
