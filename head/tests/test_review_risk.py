"""D1 risk-tiered review — pure scoring + the _ov_review wiring (source-asserted, like test_websearch).

review_risk.score is deterministic and safe-toward-`high`: an unparseable diff or any invariant-surface
touch always returns `high`, so the density fast-path can never silently under-review sensitive code.
"""
import unittest
from pathlib import Path

import review_risk


def _gitdiff(path, added_lines):
    """Minimal git-format unified diff adding the given lines to *path* (the format task_diff emits)."""
    body = "".join("+" + ln + "\n" for ln in added_lines)
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1,{len(added_lines)} @@\n" + body)


class ScoreTests(unittest.TestCase):
    def test_tiny_tests_only_is_low(self):
        tier, reasons = review_risk.score({}, _gitdiff("tests/test_x.py", ["def test_a():", "    assert True"]))
        self.assertEqual(tier, "low")
        self.assertIn("tests-only", reasons[0])

    def test_small_core_change_is_low(self):
        tier, _ = review_risk.score({}, _gitdiff("overnight.py", ["    x = 1", "    y = 2"]))
        self.assertEqual(tier, "low")

    def test_normal_change_is_medium(self):
        tier, _ = review_risk.score({}, _gitdiff("overnight.py", [f"    x{i} = {i}" for i in range(40)]))
        self.assertEqual(tier, "medium")

    def test_small_source_with_larger_test_is_low(self):
        # Finding from run ovr-20260707T083146Z: an ~8-line source change + its mandatory ~12-line test
        # is 20 lines total but only 8 CORE lines -> LOW. Counting test lines made low unreachable.
        src = _gitdiff("overnight.py", [f"    s{i} = {i}" for i in range(8)])
        test = _gitdiff("tests/test_overnight.py", [f"    self.assertTrue(x{i})" for i in range(12)])
        self.assertEqual(review_risk.score({}, src + test)[0], "low")

    def test_test_only_change_is_low(self):
        # A large TESTS-ONLY change is low risk (no production code changed).
        tier, reasons = review_risk.score(
            {}, _gitdiff("tests/test_x.py", [f"    self.assertTrue({i})" for i in range(30)]))
        self.assertEqual(tier, "low")
        self.assertIn("tests-only", reasons[0])

    def test_large_by_lines_is_high(self):
        tier, _ = review_risk.score({}, _gitdiff("overnight.py", [f"    x{i} = {i}" for i in range(130)]))
        self.assertEqual(tier, "high")

    def test_large_by_files_is_high(self):
        diff = "".join(_gitdiff(f"mod{i}.py", ["x = 1"]) for i in range(5))
        self.assertEqual(review_risk.score({}, diff)[0], "high")

    def test_invariant_surface_forces_high_even_when_tiny(self):
        for line in ("    user = _gated_identity(request)",
                     '    key = _secret_value("X", "X_FILE")',
                     "    reg.register_with_user(cid, user)"):
            tier, reasons = review_risk.score({}, _gitdiff("app.py", [line]))
            self.assertEqual(tier, "high", line)
            self.assertIn("invariant surface", reasons[0])

    def test_invariant_surface_by_filename_forces_high(self):
        # A change to cancellation.py (identity/registry) is high regardless of size.
        self.assertEqual(review_risk.score({}, _gitdiff("cancellation.py", ["    pass"]))[0], "high")

    def test_human_risk_pin_high_wins_over_small(self):
        tier, reasons = review_risk.score({"risk": "high"}, _gitdiff("overnight.py", ["    x = 1"]))
        self.assertEqual(tier, "high")
        self.assertIn("human risk", reasons[0])

    def test_empty_or_unparseable_diff_is_high(self):
        self.assertEqual(review_risk.score({}, "")[0], "high")
        self.assertEqual(review_risk.score({}, None)[0], "high")
        self.assertEqual(review_risk.score({}, "not a diff at all")[0], "high")

    def test_removed_lines_count_toward_size(self):
        # A pure deletion of 130 lines is still a large change -> high.
        diff = ("diff --git a/overnight.py b/overnight.py\n--- a/overnight.py\n+++ b/overnight.py\n@@\n"
                + "".join(f"-    old{i}\n" for i in range(130)))
        self.assertEqual(review_risk.score({}, diff)[0], "high")

    def test_thresholds_are_tunable(self):
        # With a stricter low cap, a 2-line change is no longer "low".
        diff = _gitdiff("overnight.py", ["    x = 1", "    y = 2"])
        self.assertEqual(review_risk.score({}, diff, low_max_lines=1)[0], "medium")
        # With a lower high floor, the same change becomes high.
        self.assertEqual(review_risk.score({}, diff, high_min_lines=2)[0], "high")


class OvReviewWiringTests(unittest.TestCase):
    """Verify the tier selection is actually wired into _ov_review + gated in config (source-asserted)."""

    @classmethod
    def setUpClass(cls):
        cls.app_src = Path("app.py").read_text(encoding="utf-8")
        cls.cfg_src = Path("config.py").read_text(encoding="utf-8")

    def test_config_gate_and_thresholds_present(self):
        self.assertIn("ADVERSARY_RISK_TIERING", self.cfg_src)
        for knob in ("ADVERSARY_RISK_LOW_MAX_LINES", "ADVERSARY_RISK_HIGH_MIN_LINES",
                     "ADVERSARY_RISK_LOW_MAX_ROUNDS", "ADVERSARY_RISK_MEDIUM_MAX_ROUNDS"):
            self.assertIn(knob, self.cfg_src)
        # Default OFF so the first run A/Bs against baseline.
        self.assertIn('os.getenv("ADVERSARY_RISK_TIERING", "false")', self.cfg_src)

    def test_ov_review_wires_scoring_gated(self):
        self.assertIn("import review_risk", self.app_src)
        self.assertIn("if config.ADVERSARY_RISK_TIERING:", self.app_src)
        self.assertIn("review_risk.score(", self.app_src)

    def test_low_tier_uses_pair_and_reduced_rounds(self):
        # low = the pair (2 critics, redundancy), not the lone floor; the round cap is the density lever.
        self.assertIn("tiers = tiers[:2]", self.app_src)
        self.assertIn("ADVERSARY_RISK_LOW_MAX_ROUNDS", self.app_src)
        self.assertIn("ADVERSARY_RISK_MEDIUM_MAX_ROUNDS", self.app_src)

    def test_force_adversarial_still_gets_full_ladder(self):
        # The force override must win over the risk tier (full ladder regardless).
        self.assertIn("forced_full_ladder", self.app_src)

    def test_risk_recorded_on_trail(self):
        self.assertIn('result["risk"] = risk_info', self.app_src)


class PhantomReductionWiringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app_src = Path("app.py").read_text(encoding="utf-8")
        cls.cfg_src = Path("config.py").read_text(encoding="utf-8")

    def test_config_knobs_present(self):
        for knob in ("ADVERSARY_CONTEXT_MAX_FILES", "ADVERSARY_CONTEXT_MAX_FILE_CHARS",
                     "ADVERSARY_CONTEXT_MAX_REF_LINES", "ADVERSARY_SEVERITY_GROUNDING"):
            self.assertIn(knob, self.cfg_src)

    def test_widened_packet_wired(self):
        # #1: _ov_review passes the widened knobs into build_packet.
        self.assertIn("max_files=config.ADVERSARY_CONTEXT_MAX_FILES", self.app_src)
        self.assertIn("max_ref_lines=config.ADVERSARY_CONTEXT_MAX_REF_LINES", self.app_src)

    def test_grounding_gate_wired(self):
        # #2: run_ladder is called with severity_grounding from config.
        self.assertIn("severity_grounding=config.ADVERSARY_SEVERITY_GROUNDING", self.app_src)

    def test_prompt_has_grounding_and_green_suite_rules(self):
        # #2 + #3 live in the critic prompt. (The prompt string escapes the quotes as \"violated\",
        # so match the bare token.)
        self.assertIn("GROUNDING RULE", self.app_src)
        self.assertIn("violated", self.app_src)
        self.assertIn("GREEN-SUITE FACT", self.app_src)


if __name__ == "__main__":
    unittest.main()
