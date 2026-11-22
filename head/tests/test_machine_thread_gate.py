"""Machine packets must name an existing chat_id.\n\nThe code rejects machine-originated requests that fail to resolve a specific\nchat_id (e.g., by missing id), preventing silent fallback into an active\nhuman session. Ephemeral one-shots are exempt."""
import pathlib
import tempfile
import unittest
from unittest import mock

import app
import config
import history


MACHINE = "ClaudeSupervisor"


class MachineThreadGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self._tmp.name)
        self.assertIn(MACHINE, config.MACHINE_USERS,
                      "fixture assumes the default machine identity is configured")

    def tearDown(self):
        history.HISTORY_DIR = self._orig_dir
        self._tmp.cleanup()

    # --- the owning component's check (history.machine_chat_error) ---

    def test_human_users_are_untouched(self):
        """The gate must not exist for humans — their active-chat fallback is a FEATURE (#18)."""
        self.assertIsNone(history.machine_chat_error("Alex", None))
        self.assertIsNone(history.machine_chat_error("Alex", "no-such-chat"))

    def test_machine_user_with_no_chat_id_is_refused(self):
        err = history.machine_chat_error(MACHINE, None)
        self.assertIsNotNone(err, "no chat_id must refuse — this fallback is the whole defect")
        self.assertIn(MACHINE, err)
        self.assertIn("existing chat_id", err)

    def test_machine_user_with_a_missing_chat_id_is_refused(self):
        history.new_chat(MACHINE)   # an active chat EXISTS — the fallback would have swallowed this
        self.assertIsNotNone(history.machine_chat_error(MACHINE, "aaaabbbbcccc"))

    def test_machine_user_with_an_existing_chat_id_passes(self):
        entry = history.new_chat(MACHINE)
        self.assertIsNone(history.machine_chat_error(MACHINE, entry["id"]))

    # --- the app seam (_machine_gate + _run_query_core wiring) ---

    def test_ephemeral_one_shots_are_exempt(self):
        """Evals and probes run ephemeral; they never persist, so the gate must not block them."""
        q = app.Q(text="probe", user=MACHINE, ephemeral=True)
        self.assertIsNone(app._machine_gate(q))

    def test_run_query_core_refuses_before_any_model_work(self):
        """WIRING proof: the refusal comes out of the real query path as a route-error dict.
        No mocks on the gate itself — if the call in _run_query_core is ever dropped, this test
        reaches the model layer and fails loudly there instead of passing."""
        q = app.Q(text="packet", user=MACHINE)
        result = app._run_query_core(q)
        self.assertEqual(result.get("route"), "error")
        self.assertIn("existing chat_id", result.get("message", ""))


if __name__ == "__main__":
    unittest.main()
