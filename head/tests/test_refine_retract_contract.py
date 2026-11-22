import unittest

import deliberation


class RefineContract(unittest.TestCase):
    def test_retract_move_exists(self):
        self.assertIn("REMOVE the claim", deliberation.TH_REFINE_RULES)
        self.assertIn("self-report", deliberation.TH_REFINE_RULES)

    def test_honest_inability_is_named_as_passing(self):
        self.assertIn("honest inability statement passes review", deliberation.TH_REFINE_RULES)

    def test_no_tool_no_reading(self):
        self.assertIn("NEVER present a status, count, or reading that no tool produced",
                      deliberation.TH_REFINE_RULES)

    def test_the_licensing_phrase_is_gone(self):
        """The exact words that licensed keeping an unverified claim must not survive anywhere in
        the contract — this is the phrase the E1 fabrication rode in on."""
        self.assertNotIn("answer with what you already have", deliberation.TH_REFINE_RULES)

    def test_rejection_requires_the_trail(self):
        """The refiner may no longer overrule its reviewer on vibes."""
        self.assertIn("ONLY when the evidence trail directly contradicts",
                      deliberation.TH_REFINE_RULES)
        self.assertNotIn("reject unsupported findings", deliberation.TH_REFINE_RULES)

    def test_catch_up_is_in_the_tool_menu(self):
        """The orientation tool must be NAMED where the model is told to gather evidence."""
        self.assertIn("catch_up", deliberation.TH_REFINE_RULES)

    def test_app_uses_the_shared_constant(self):
        import inspect
        import app
        self.assertIn("deliberation.TH_REFINE_RULES", inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
