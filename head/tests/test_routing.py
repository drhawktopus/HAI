"""Tests for the Phase-2 routing dropdown (Auto / Force 27B / Quick 8B).

`config` is stdlib-only so its behavior is exercised directly; `app` and `tools` pull in heavy
deps not present in the runner env, so those are verified with static source-wiring checks (same
pattern as test_gating / test_heavy_suffix)."""
import unittest
from pathlib import Path

import config

APP = Path("app.py").read_text(encoding="utf-8")
TOOLS = Path("tools.py").read_text(encoding="utf-8")


class RoutingConfigTests(unittest.TestCase):
    def test_the_27b_pin_is_every_users(self):
        """Per-user routing lists are retired. A pin to a busy instance must queue (with ETA/reroute), not hide the box or drop the pin."""
        self.assertFalse(hasattr(config, "MOTHERSHIP_FORCE_USERS"))
        self.assertNotIn("config.MOTHERSHIP_FORCE_USERS", APP, "no code reads the retired list (a comment may name it)")
        self.assertNotIn("force_routing_users", APP, "the page no longer gates the 27B option per user")
        self.assertIn("PRIORITY_USERS", APP, "Alex-first in the slot is the guard the gate used to be")

    def test_test_accounts_are_not_admins(self):
        'Test accounts must never be admins. `ADMIN_USERS` grants Home Assistant device control,\nso allowing a test user admin access would be a critical security risk.'
        for u in config.ADMIN_USERS:
            self.assertFalse(config.is_test_user(u),
                             "test account %r is an ADMIN — it would gain HA device control" % u)

    def test_brain_window_is_a_measured_resident_fit(self):
        """The brain's window size must ensure the model is fully resident in VRAM.

        19,968 is the desktop-aware fit on BigHonker's 3070 (campaigns/fit-20261019-desktop);
        20,480 spills beside a 594 MiB desktop. It must equal BigHonker's triage_local row or ollama
        reloads the model whenever chat and factory work alternate. Change it only from a fresh fit;
        hard-bench test_ctx_stores_agree.py
        pins this value, triage_local.json and the menu's table together."""
        self.assertEqual(config.BRAIN_NUM_CTX, 19968)
        self.assertIn("config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX", APP)
        self.assertIn("config.FAILOVER_HOST, config.FAILOVER_MODEL, config.FAILOVER_NUM_CTX", APP)


class RoutingWiringTests(unittest.TestCase):
    def test_routing_modes_and_field(self):
        self.assertIn('ROUTING_MODES = ("auto", "force_27b", "quick_8b")', APP)
        self.assertIn('routing: str = "auto"', APP)
        self.assertIn("if q.routing not in ROUTING_MODES:", APP)

    def test_phrase_regex_removed(self):
        # The ambiguous text-phrase router must be gone; the external (claude/codex) one stays.
        self.assertNotIn("MOTHERSHIP_REQUEST", APP)
        self.assertIn("EXTERNAL_REQUEST", APP)

    def test_force_is_every_users_and_a_nursing_submode_routes_nothing(self):
        # The 27B pin is honoured for every user (the slot queues it); a Nursing sub-mode is the brief, never a route.
        self.assertIn('force_lead = (q.routing == "force_27b") or', APP)
        self.assertNotIn('nmode in ("ebp", "educator")', APP)
        self.assertNotIn('nmode == "reference"', APP)

    def test_quick_strips_escalation_but_may_use_mothership_small_brain(self):
        # Quick strips escalation tool (blocks 27B), but brain cascade may fallback to Mothership SMALL before Softserve.
        self.assertIn('quick_8b = (q.routing == "quick_8b")', APP)
        self.assertIn("b_host, b_model, b_ctx, b_tools, b_name = pick_brain()", APP)
        self.assertIn("def pick_brain(allow_lead=True):", APP)
        # Quick mode strips the escalation tool so the brain can't hand off to the 27B.
        self.assertIn("tools.without_escalation(b_tools)", APP)

    def test_without_escalation_helper(self):
        self.assertIn("def without_escalation(tools_list):", TOOLS)
        self.assertIn('t["function"]["name"] != "escalate_to_mothership"', TOOLS)

    def test_every_persona_keeps_the_model_and_effort_boxes(self):
        self.assertNotIn("modelAxis.style.display = nursing", APP)
        self.assertNotIn("if (nursing) model.value = 'auto'", APP)
        self.assertIn("modelAxis.style.display = '';", APP)

    def test_an_extreme_electricity_price_holds_auto_with_a_notice_and_never_a_pin(self):
        # #34 area C step 2: the hold is read off the weighing (scheduler.weigh_rows marks the band `held`), told as the
        # turn's answer, and sits AFTER the pinned routes - a pin never reaches the weighing (checklist D3)
        self.assertIn('if _weighed and _weighed[3] and _weighed[3][0].get("held"):', APP)
        self.assertIn('route="held"', APP)
        self.assertLess(APP.index('if force_lead'), APP.index('_weighed[3][0].get("held")'))

    def test_ui_axes_model_and_effort(self):
        # Model selector (Auto/8B/27B -> routing) and Effort selector (Normal/Hard -> effort_mode).
        # Hard effort is unavailable when the model is pinned to 8B.
        # An exact row pin (row:<box>/<model>) overrides the model selection by forcing routing to 'auto'.
        self.assertIn("const modelVal = model.value", APP)
        self.assertIn("const routingVal = pinRowVal ? 'auto' : modelVal;", APP)
        self.assertIn("routing:routingVal", APP)
        self.assertIn("effort_mode:effortVal", APP)
        self.assertIn('id="modeDesc"', APP)
        self.assertIn('id="model"', APP)
        self.assertIn('id="effort"', APP)
        self.assertIn("routingVal !== 'quick_8b' && effort.value === 'think_hard'", APP)
        # The 27B option is available to all users; do not add per-user gating here.
        self.assertNotIn("forceUsers", APP)

    def test_ui_has_user_splash_gate(self):
        # Users must pick a profile on a splash screen before chatting.
        self.assertIn('id="gate"', APP)
        self.assertIn("function selectUser(", APP)
        self.assertIn("gate.classList.add('hidden')", APP)

    def test_deliberation_is_conditional_not_forced(self):
        # think_hard must not force Mothership; deliberation only executes when the request lands on the 27B via _dispatch_to_lead.
        self.assertIn('deliberate = q.effort_mode == "think_hard"', APP)
        self.assertEqual(APP.count("_dispatch_to_lead("), 7)  # 1 def + 6 call sites
        self.assertIn("if deliberate:", APP)
        self.assertIn("return _run_think_hard(", APP)


class NursingPreviewTests(unittest.TestCase):
    """Admins can preview the nursing tutor from their own (homelab) profile by picking a nursing
    sub-mode: it runs the full nursing experience grounded in Riley's corpus."""

    def test_preview_namespace_default(self):
        # Preview now grounds in the SHARED nursing corpus (general reference), not Riley's private one.
        self.assertEqual(config.NURSING_PREVIEW_NAMESPACE, "nursing_shared")
        self.assertTrue(config.is_nursing_namespace(config.NURSING_PREVIEW_NAMESPACE))

    def test_backend_admin_preview_gate(self):
        # Selecting a nursing sub-mode flips on nursing_mode + re-grounds in the nursing corpus,
        # but only for admins (so ordinary users can't opt into it).
        self.assertIn("q.mode in NURSING_MODES", APP)
        self.assertIn("q.user in config.ADMIN_USERS", APP)
        self.assertIn("ns = config.NURSING_PREVIEW_NAMESPACE", APP)

    def test_modes_exposes_admin_users(self):
        self.assertIn('"admin_users": sorted(config.ADMIN_USERS)', APP)

    def test_ui_offers_personas_as_hats(self):
        self.assertIn("function isAdminUser(){ return adminUsers.includes(user); }", APP)
        # EVERY user can wear General / Nursing / Law (a hat, not a lock); the default is just the start.
        self.assertIn("const pOpts = [['general','General'],['nursing','Nursing'],['law','Law']];", APP)
        self.assertIn("const defP = defaultPersonas[user] || 'general';", APP)
        # The Nursing persona must not hide the Model and Effort axes; these controls remain visible for all personas.
        self.assertIn("modelAxis.style.display = '';", APP)
        self.assertNotIn("nursing ? 'none'", APP)
        # The mode field carries the nursing sub-mode whenever the Nursing persona is active.
        self.assertIn("const modeVal = (persona.value === 'nursing') ? subMode.value : '';", APP)
        # The selected persona is sent so the server can decouple behavior from identity.
        self.assertIn("persona:persona.value", APP)
        self.assertIn("adminUsers = d.admin_users || []", APP)


if __name__ == "__main__":
    unittest.main()
