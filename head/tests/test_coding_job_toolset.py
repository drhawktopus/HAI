"""Verifies the machine user's supervised coding job receives the full CODING SET.

The standard selection path truncates the coding tool family based on retrieval scores; the purpose-built set bypasses this to ensure no critical tools (like `coding_create`) are dropped. The test environment mocks external dependencies to prevent persistence or model calls."""
import contextlib
import io
import unittest
from unittest import mock

import app
import config
import tool_selection
import tools

MACHINE = "pintest-machine"
HUMAN = "pintest-admin"

PACKET = ("SUPERVISED LOCAL CODING JOB\nSupervisor: Claude\nWorkspace: orchestrator-real\n"
          "Objective: create one new file and run the suite.")

SURVEY_PACKET = ("SUPERVISED LOCAL CODING JOB\nSupervisor: decomposed-driver (gather pass)\n"
                 "Workspace: orchestrator-real\n" + app.SURVEY_MODE_LINE + "\n\n"
                 "Read the code and output a grounding block.")


class _DispatchCase(unittest.TestCase):
    """Capture the toolset _dispatch_lead hands to the model call."""

    def setUp(self):
        self.handed = []

        def fake_chat(host, model, messages, tools_list, num_ctx, **kw):
            self.handed.append(tools_list)
            return "ok", None

        self._patch(app.ollama_client, "chat", fake_chat)
        self._patch(app, "_persist", lambda *a, **k: None)
        self._patch(config, "ADMIN_USERS", {HUMAN, MACHINE})
        self._patch(config, "MACHINE_USERS", {MACHINE})

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def dispatch(self, user, text):
        q = app.Q(text=text, user=user, ephemeral=True)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": text}]
        out = app._dispatch_lead(q, text, messages, [], False, lambda *a, **k: None, "")
        self.assertEqual(out.get("route"), "heavyweight")
        names = {t["function"]["name"] for t in self.handed[0]}
        return names


class TheSetItself(unittest.TestCase):

    def test_every_coding_tool_is_in_the_set(self):
        """The regression that started this: a job lost coding_create mid-run."""
        set_names = {t["function"]["name"] for t in tools.CODING_JOB_TOOLS}
        for t in tools.CODING_TOOLS:
            self.assertIn(t["function"]["name"], set_names)
        self.assertIn("coding_create", set_names)

    def test_the_set_survives_selection_unchanged(self):
        """The guarantee mechanism is selection's own under-budget rule - no boost, no override."""
        out, note = tool_selection.select(list(tools.CODING_JOB_TOOLS), MACHINE, PACKET,
                                          config.HEAVY_NUM_CTX)
        self.assertEqual([t["function"]["name"] for t in out],
                         [t["function"]["name"] for t in tools.CODING_JOB_TOOLS])
        self.assertIsNone(note)

    def test_survey_subset_is_readonly(self):
        """Nothing that edits, runs, or opens work - and no member outside the job set."""
        job_names = {t["function"]["name"] for t in tools.CODING_JOB_TOOLS}
        survey_names = {t["function"]["name"] for t in tools.CODING_SURVEY_TOOLS}
        self.assertTrue(survey_names <= job_names)
        forbidden = {"coding_patch", "coding_create", "coding_file", "coding_test",
                     "coding_task", "coding_checkpoint", "coding_review",
                     "coding_project_driver", "coding_decision", "coding_escalation",
                     "journal_append"}
        self.assertFalse(survey_names & forbidden)
        self.assertIn("coding_read", survey_names)
        self.assertIn("coding_search", survey_names)


class TheDispatchSeam(_DispatchCase):

    def test_machine_supervised_gets_the_coding_set(self):
        names = self.dispatch(MACHINE, PACKET)
        self.assertEqual(names, {t["function"]["name"] for t in tools.CODING_JOB_TOOLS})

    def test_human_supervised_keeps_the_full_toolset(self):
        names = self.dispatch(HUMAN, PACKET)
        self.assertEqual(names, {t["function"]["name"] for t in tools.HEAVY_TOOLS})

    def test_machine_ordinary_text_keeps_the_full_toolset(self):
        names = self.dispatch(MACHINE, "what is playing on plex tonight?")
        self.assertEqual(names, {t["function"]["name"] for t in tools.HEAVY_TOOLS})


class AnyBoxThePlacementChose(unittest.TestCase):
    """A job the weighing places on a brain box (BigHonker, a small model on Mothership) gets the same job set as one
    served by the lead: parallel jobs go to other boxes, and a job without its coding set cannot open its workspace."""

    def setUp(self):
        p = mock.patch.object(config, "MACHINE_USERS", {MACHINE})
        p.start()
        self.addCleanup(p.stop)

    def test_a_machine_packet_on_a_brain_box_gets_the_coding_set(self):
        self.assertIs(app._served_toolset(MACHINE, PACKET, tools.BRAIN_TOOLS), tools.CODING_JOB_TOOLS)

    def test_a_machine_survey_packet_gets_the_survey_subset(self):
        self.assertIs(app._served_toolset(MACHINE, SURVEY_PACKET, tools.BRAIN_TOOLS), tools.CODING_SURVEY_TOOLS)

    def test_every_other_turn_keeps_its_placed_pool(self):
        self.assertIs(app._served_toolset(HUMAN, PACKET, tools.BRAIN_TOOLS), tools.BRAIN_TOOLS)
        self.assertIs(app._served_toolset(MACHINE, "what is the weather", tools.BRAIN_TOOLS), tools.BRAIN_TOOLS)


class TheThinkHardSeam(unittest.TestCase):
    """The seam actually in force for Absurd runs. The dispatch pin above was green while a live
    supervised run had no coding_read: think-hard passes handed chat their own toolset. Both
    consumers now share ONE derivation (_heavy_toolset); this pins the think-hard half."""

    def setUp(self):
        self.handed = []

        def capture_then_stop(host, model, messages, tools_list, num_ctx, **kw):
            self.handed.append(tools_list)
            # Deliberate stop once the toolset is captured — this test inspects only the toolset
            # handed to chat(). The message self-identifies as a harness artifact; think() also
            # swallows the stdout this abort emits so it can't read as a real failure.
            raise app.ollama_client.ChatError(
                host, model, "[test-harness] toolset captured; stopping before the live pass (expected, not a failure)")

        self._patch(app.ollama_client, "chat", capture_then_stop)
        # _run_think_hard returns before chat() when the heavy host reads busy; that is LIVE hardware
        # state (a Mothership running a compute order turned these four red), not what is pinned here.
        self._patch(app, "_ensure_lead", lambda *_a, **_k: True)
        self._patch(config, "ADMIN_USERS", {HUMAN, MACHINE})
        self._patch(config, "MACHINE_USERS", {MACHINE})

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def think(self, user, text):
        q = app.Q(text=text, user=user, ephemeral=True, effort_mode="think_hard")
        # _run_think_hard renders the mocked chat's ChatError through _error_message, which prints
        # "<label> failed: ..." to stdout; swallow it so this expected abort can't leak a "failed"
        # line into `unittest discover` output that a coding_test reader scans as a real failure.
        with contextlib.redirect_stdout(io.StringIO()):
            out = app._run_think_hard(q, text, [{"role": "system", "content": "sys"}], [], False,
                                      lambda *a, **k: None)
        self.assertIsInstance(out, dict)
        return {t["function"]["name"] for t in self.handed[0]}

    def test_machine_supervised_think_hard_gets_the_coding_set(self):
        names = self.think(MACHINE, PACKET)
        self.assertEqual(names, {t["function"]["name"] for t in tools.CODING_JOB_TOOLS})

    def test_human_think_hard_keeps_the_full_toolset(self):
        names = self.think(HUMAN, "plan my week around the weather")
        self.assertEqual(names, {t["function"]["name"] for t in tools.HEAVY_TOOLS})

    def test_machine_survey_packet_gets_the_readonly_subset(self):
        names = self.think(MACHINE, SURVEY_PACKET)
        self.assertEqual(names, {t["function"]["name"] for t in tools.CODING_SURVEY_TOOLS})

    def test_mentioning_survey_midline_does_not_narrow(self):
        """Mentioning a thing is not being one - only the driver's own mode line narrows."""
        names = self.think(MACHINE, PACKET + "\nContext: the isolated read-only survey found gaps.")
        self.assertEqual(names, {t["function"]["name"] for t in tools.CODING_JOB_TOOLS})


if __name__ == "__main__":
    unittest.main()
