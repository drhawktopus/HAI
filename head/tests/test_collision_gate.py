import time
import unittest
from unittest import mock

import httpx
import comfy_client
import config
import ollama_client

# No unit test may wait on a network timeout - enforced by tests/_no_live_probes.py; mock instead.
from _no_live_probes import start_no_live_probes, stop_no_live_probes

_TRANSPORT = []


def setUpModule():
    start_no_live_probes()
    # Patch transport to fail immediately. This ensures tests assert on the gate's verdict,
    # not on network latency, and prevents real HTTP calls in unit tests.
    for name in ("post", "stream", "get"):
        p = mock.patch("httpx.%s" % name,
                       side_effect=httpx.ConnectError("no ollama in unit tests"))
        p.start()
        _TRANSPORT.append(p)
    # Patch _open_chat_stream: ollama_client instantiates its own httpx client, so patching httpx directly misses its socket.
    p = mock.patch.object(ollama_client, "_open_chat_stream",
                          side_effect=httpx.ConnectError("no ollama in unit tests"))
    p.start()
    _TRANSPORT.append(p)
    p = mock.patch.object(config, "CHAT_RETRIES", 0)
    p.start()
    _TRANSPORT.append(p)


def tearDownModule():
    while _TRANSPORT:
        _TRANSPORT.pop().stop()
    stop_no_live_probes()


class CollisionGateTests(unittest.TestCase):

    def setUp(self):
        self._heavy = config.HEAVY_HOST
        config.HEAVY_HOST = "http://heavy:11434"

    def tearDown(self):
        config.HEAVY_HOST = self._heavy

    def _chat(self):
        return ollama_client.chat(config.HEAVY_HOST, "m", [{"role": "user", "content": "hi"}], [])

    def test_training_refuses_immediately_without_waiting(self):
        """Hours, not minutes: fail fast so the caller can downgrade instead of stalling."""
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=({"state": "training"}, 10)), \
                mock.patch.object(comfy_client, "render_busy", return_value=True), \
                mock.patch.object(time, "sleep", side_effect=AssertionError("must not wait")):
            t0 = time.time()
            with self.assertRaises(ollama_client.ChatError) as ctx:
                self._chat()
            self.assertLess(time.time() - t0, 5, "a training card must not be waited on")
            self.assertIn("training", str(ctx.exception).lower())

    def test_STALE_training_heartbeat_also_refuses_immediately(self):
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any",
                               return_value=({"state": "training"}, 4000)), \
                mock.patch.object(comfy_client, "render_busy", return_value=True), \
                mock.patch.object(time, "sleep", side_effect=AssertionError("must not wait")):
            with self.assertRaises(ollama_client.ChatError) as ctx:
                self._chat()
            self.assertIn("training", str(ctx.exception).lower())

    def test_ancient_heartbeat_is_not_treated_as_a_live_run(self):
        """Past the 24h ceiling the file is an artefact — fall through to the normal path."""
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any",
                               return_value=({"state": "training"}, 90000)), \
                mock.patch.object(comfy_client, "render_busy", return_value=False):
            # Only the GATE's verdict is under test: a transport failure past the gate is fine, but
            # it must not be the gate refusing on a day-old claim.
            try:
                self._chat()
            except ollama_client.ChatError as exc:
                self.assertNotIn("training", str(exc).lower())
            except Exception:
                pass

    def test_still_busy_after_the_wait_refuses_instead_of_dispatching(self):
        """The original bug: after the bounded wait it proceeded ONTO the busy card."""
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=({"state": "rendering"}, 10)), \
                mock.patch.object(comfy_client, "render_busy", return_value=True), \
                mock.patch.object(config, "COLLISION_GATE_WAIT", 0), \
                mock.patch.object(ollama_client, "_chat_once") as once:
            with self.assertRaises(ollama_client.ChatError):
                self._chat()
            once.assert_not_called()

    def test_a_free_card_is_not_gated_at_all(self):
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=({"state": "free"}, 10)), \
                mock.patch.object(comfy_client, "render_busy", return_value=False), \
                mock.patch.object(time, "sleep", side_effect=AssertionError("must not wait")), \
                mock.patch.object(ollama_client, "_chat_once",
                                  return_value={"message": {"content": "ok"}}) as once:
            self._chat()
            once.assert_called()

    def test_a_broken_gate_never_fails_the_call(self):
        """A gate that throws must not become an outage — the daemon is the safety net."""
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", side_effect=RuntimeError("probe down")), \
                mock.patch.object(comfy_client, "render_busy", side_effect=RuntimeError("down")), \
                mock.patch.object(ollama_client, "_chat_once",
                                  return_value={"message": {"content": "ok"}}) as once:
            self._chat()
            once.assert_called()

    def test_non_heavy_hosts_skip_the_gate_entirely(self):
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=({"state": "training"}, 10)), \
                mock.patch.object(ollama_client, "_chat_once",
                                  return_value={"message": {"content": "ok"}}) as once:
            ollama_client.chat("http://bighonker:11434", "m",
                               [{"role": "user", "content": "hi"}], [])
            once.assert_called()


if __name__ == "__main__":
    unittest.main()
