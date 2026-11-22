"""Camera tools must be reachable in both BRAIN_TOOLS (9B) and HEAVY_TOOLS (27B). tool_selection.select() only narrows existing pools, so if a tool is missing from a pool, it will never be selectable for requests routed to that model tier."""
import unittest

import tools


def _names(tool_list):
    return {(t.get("function") or {}).get("name") for t in tool_list
            if isinstance(t, dict)}


class CameraHeavyParityTests(unittest.TestCase):
    def test_camera_tools_are_candidates_on_the_27b_pool(self):
        heavy = _names(tools.HEAVY_TOOLS)
        for cam in ("camera_events", "camera_last_seen", "camera_status",
                    "camera_scan_footage"):
            self.assertIn(
                cam, heavy,
                "%s missing from HEAVY_TOOLS: the warm-27B path cannot offer it, "
                "and select() only narrows a pool." % cam)

    def test_only_mothership_escalation_is_brain_only(self):
        # The ONE legitimate BRAIN-minus-HEAVY member is escalate_to_mothership
        # (the 27B IS Mothership, so it must not offer self-escalation). Anything
        # else here is a home capability silently unreachable on the 27B -- the
        # exact class of bug this pin exists to catch.
        brain = _names(tools.BRAIN_TOOLS)
        heavy = _names(tools.HEAVY_TOOLS)
        self.assertEqual(brain - heavy, {"escalate_to_mothership"})


if __name__ == "__main__":
    unittest.main()
