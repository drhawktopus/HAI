"""A hung box costs one timeout per quiet window, not one per question.

The probe answers are unchanged ("could not ask"); only the wait goes."""
import os
import unittest
from unittest import mock

import httpx

import config
import ollama_client as oc
import scheduler


class ProbeQuiet(unittest.TestCase):
    def setUp(self):
        oc._PROBE_QUIET.clear()

    def tearDown(self):
        oc._PROBE_QUIET.clear()

    def test_a_timeout_quiets_the_box_and_the_answer_is_unchanged(self):
        with mock.patch.object(oc.httpx, "get", side_effect=httpx.ConnectTimeout("hung")) as get:
            self.assertIsNone(oc.models_resident("http://10.9.9.9:11434"))
            self.assertIsNone(oc.models_resident("http://10.9.9.9:11434"), "still 'could not ask'")
            self.assertFalse(oc.reachable("http://10.9.9.9:11434"))
        self.assertEqual(get.call_count, 1, "one wait per quiet window, not one per question")

    def test_an_answer_or_the_window_ending_asks_again(self):
        ok = mock.Mock()
        ok.json.return_value = {"models": [{"name": "tev1:latest"}]}
        with mock.patch.object(config, "PROBE_QUIET_S", 0.0), \
                mock.patch.object(oc.httpx, "get", side_effect=httpx.ReadTimeout("hung")):
            oc.models_resident("http://10.9.9.8:11434")              # a window that has already ended
        with mock.patch.object(oc.httpx, "get", return_value=ok) as get:
            self.assertEqual(oc.models_resident("http://10.9.9.8:11434"), ["tev1:latest"])
        self.assertEqual(get.call_count, 1)
        self.assertFalse(oc.probe_quiet("http://10.9.9.8:11434"), "an answer clears the window")

    def test_gpu_health_shares_the_window_and_still_reads_ok(self):
        with mock.patch.object(scheduler, "_gpu_state_rec", return_value=None, create=True), \
                mock.patch.object(oc.httpx, "get", side_effect=httpx.ReadTimeout("hung")) as get:
            first = scheduler.gpu_health(scheduler.LEAD_BOX)
            second = scheduler.gpu_health(scheduler.LEAD_BOX)
        self.assertEqual(get.call_count, 1, "the residency check waits once per quiet window")
        self.assertEqual((first[0], second[0]), (True, True), "could not ask is never dead")

    def test_the_dataset_registry_asks_an_unanswering_mothership_once_per_window(self):
        import tools
        tools._REGISTRY_CACHE.update({"data": None, "at": 0.0, "failed_at": 0.0})
        with mock.patch("lead_ssh.dataset_registry", return_value={}) as ask:
            self.assertEqual(tools._dataset_registry(), {})
            self.assertEqual(tools._dataset_registry(), {}, "still 'could not ask'")
        self.assertEqual(ask.call_count, 1, "one ask per quiet window")
        with mock.patch("lead_ssh.dataset_registry", return_value={"met": {}}) as ask:
            self.assertEqual(tools._dataset_registry(force=True), {"met": {}}, "force asks now")
        tools._REGISTRY_CACHE.update({"data": None, "at": 0.0, "failed_at": 0.0})

    def test_a_refusal_is_not_a_timeout_and_glances_shares_the_window(self):
        with mock.patch.object(oc.httpx, "get", side_effect=httpx.ConnectError("refused")):
            oc.reachable("http://10.9.9.7:11434")
        self.assertFalse(oc.probe_quiet("http://10.9.9.7:11434"), "a fast refusal costs nothing - no window")
        # Glances is only dialled where a box's endpoint is configured, so the test names it.
        with mock.patch.dict(os.environ, {config._roster_device(scheduler.LEAD_BOX)["prefix"] + "_GLANCES":
                                          "http://glances.test:61208/api/4"}), \
                mock.patch("httpx.get", side_effect=httpx.ConnectTimeout("hung")) as get:
            self.assertIsNone(scheduler._glances(scheduler.LEAD_BOX, "mem"))
            self.assertIsNone(scheduler._glances(scheduler.LEAD_BOX, "cpu"))
        self.assertEqual(get.call_count, 1, "the Glances port is quieted after its own timeout")
