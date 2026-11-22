"""
Ensure deterministic side-effecting routes do not fire on large machine payloads,
while remaining accessible for standard short-text commands.

Routes match via regex containment (e.g., `_DATASET_CANDIDATES_RE`), lacking explicit
length bounds in the route handlers. This tests that valid user commands (typically
<200 chars) still trigger actions, while oversized automated payloads are rejected."""
import unittest

import app


REAL_COMMANDS = [
    "Show me the candidates",
    "show me the candidates",
    "Show me the next candidates",
    "show me more female candidates to pick from",
]

# The real payload that tripped the parser, trimmed to its triggering sentence plus enough bulk
# to be unmistakably a machine payload. Preserved BYTE-FOR-BYTE - exactness is what reproduces it.
INCIDENT_PACKET = (
    "SUPERVISED LOCAL CODING JOB - continue\n"
    "Supervisor: Claude (session 4)\n"
    "Task: 6222edfac23a40d09b8ea4d281015645 (already created, isolated worktree)\n\n"
    "Resume that task and WRITE THE FILES NOW. Skip further reading - you have what you need.\n"
    "Every failure is LOUD and NAMED in the returned text, never an empty section: topic matched\n"
    "nothing (give the filter AND the candidate count) - anything truncated (say so).\n"
)


class TheBoundIsCalibratedToRealUsage(unittest.TestCase):
    def test_every_real_command_is_within_the_bound(self):
        for cmd in REAL_COMMANDS:
            self.assertTrue(app._is_short_command(cmd),
                            f"real command rejected by the bound: {cmd!r} ({len(cmd)} chars)")

    def test_the_longest_real_command_has_real_headroom(self):
        longest = max(len(c) for c in REAL_COMMANDS)
        self.assertLessEqual(longest, 50, "history sample changed — re-measure before trusting this")
        self.assertGreaterEqual(app.DIRECT_ROUTE_MAX_CHARS, longest * 4,
                                "bound is too close to observed usage to absorb a longer phrasing")

    def test_the_incident_packet_is_outside_the_bound(self):
        self.assertFalse(app._is_short_command(INCIDENT_PACKET))

    def test_whitespace_padding_cannot_sneak_a_payload_in(self):
        self.assertFalse(app._is_short_command("   " + INCIDENT_PACKET + "   \n\n"))

    def test_empty_and_none_are_handled(self):
        self.assertTrue(app._is_short_command(""))
        self.assertTrue(app._is_short_command(None))


class TheIncidentStillMatchesTheRegex(unittest.TestCase):
    """POSITIVE CONTROL for the whole fix. If the regex stopped matching, these tests would pass for
    the wrong reason and prove nothing about the size guard."""

    def test_the_packet_DOES_match_the_dataset_intent(self):
        m = app._DATASET_CANDIDATES_RE.search(INCIDENT_PACKET)
        self.assertIsNotNone(m, "the regex no longer matches — this suite is no longer testing the guard")
        self.assertIn("candidate", m.group(0))

    def test_a_render_packet_DOES_match_the_refine_intent(self):
        text = "Objective: show the closer match first, then the wider set."
        self.assertIsNotNone(app.P20_REFINE_CLOSER.search(text))


class RouteGuardsAreWired(unittest.TestCase):
    """Source-text, used narrowly: the property is 'the guard is IN the condition', and the branches
    are inside a 400-line request handler that needs a live model to execute."""

    def setUp(self):
        import inspect
        self.src = inspect.getsource(app)

    def test_dataset_route_gates_on_the_bound(self):
        self.assertIn("_is_short_command(t)\n            and _DATASET_CANDIDATES_RE.search(t)", self.src)

    def test_p20_refine_route_gates_on_the_bound(self):
        self.assertIn("and _is_short_command(t) and not want_p20_status", self.src)

    def test_every_MUTATING_deterministic_route_is_guarded(self):
        """Counts them. A third side-effecting route added later without the guard trips this."""
        self.assertEqual(self.src.count("_is_short_command(t)"), 2,
                         "a deterministic route was added or removed — confirm it mutates nothing, "
                         "or guard it too")


if __name__ == "__main__":
    unittest.main()
