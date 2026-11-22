"""
Test that context downgrades are explicitly logged.

Downgrades alter the model configuration, rendering performance measurements
invalid unless recorded. Non-memory errors must not trigger downgrades, as
toggling the context window reloads the model and invalidates the prompt cache."""
import unittest
from unittest import mock

import httpx

import config
import notes
import ollama_client


class CtxDowngradeIsLogged(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self._orig = notes.log
        notes.log = lambda line: self.logged.append(str(line))

    def tearDown(self):
        notes.log = self._orig

    def _reply(self, text="ok"):
        return {"message": {"content": text}, "done_reason": "stop",
                "prompt_eval_count": 10, "eval_count": 5}

    def test_downgrade_after_a_memory_error_is_logged_LOUDLY(self):
        """First attempt fails for MEMORY -> the retry runs at half the window. That MUST be recorded."""
        calls = []

        def fake_once(host, model, messages, tools_list, ctx, keep_alive, timeout):
            calls.append(ctx)
            if len(calls) == 1:
                raise ValueError("model requires more system memory than is available (failed to allocate KV)")
            return self._reply()

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
             mock.patch.object(ollama_client.time, "sleep", lambda *_: None):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 65536)

        self.assertEqual(calls[0], 65536, "first attempt should use the requested window")
        self.assertLess(calls[1], 65536, "the retry should have downgraded — fixture is wrong if not")

        lines = [l for l in self.logged if "CTX DOWNGRADE" in l]
        self.assertTrue(lines, "the downgrade was SILENT — this is the whole defect")
        line = lines[0]
        self.assertIn("requested=65536", line)
        self.assertIn(f"using={calls[1]}", line)
        self.assertIn("VOID", line, "the line must tell a reader that measurements from it are void")

    def test_NO_log_when_the_full_window_was_used(self):
        """POSITIVE CONTROL, inverted: a clean call must NOT emit the line, or the signal is noise
        and gets ignored — a log that always fires is the same as a log that never does."""
        with mock.patch.object(ollama_client, "_chat_once",
                               side_effect=lambda *a, **k: self._reply()):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 65536)
        self.assertEqual([l for l in self.logged if "CTX DOWNGRADE" in l], [])

    def test_the_ladder_still_floors_at_8192(self):
        """Repeated MEMORY errors walk the ladder down, never below 8192."""
        seen = []

        def fake_once(host, model, messages, tools_list, ctx, keep_alive, timeout):
            seen.append(ctx)
            if len(seen) <= config.CHAT_RETRIES:
                raise ValueError("out of memory")
            return self._reply()

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
             mock.patch.object(ollama_client.time, "sleep", lambda *_: None):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 65536)
        self.assertGreaterEqual(min(seen), 8192, "the floor must not have moved")
        self.assertEqual(seen[0], 65536)
        self.assertLess(seen[1], 65536, "a memory error must still shrink the window - fixture is wrong if not")

    def test_a_non_memory_error_retries_at_the_SAME_window(self):
        seen = []

        def fake_once(host, model, messages, tools_list, ctx, keep_alive, timeout):
            seen.append(ctx)
            if len(seen) <= config.CHAT_RETRIES:
                raise (httpx.ConnectError("boom") if len(seen) == 1 else ValueError('{"error":"no user query found in messages"}'))
            return self._reply()

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
             mock.patch.object(ollama_client.time, "sleep", lambda *_: None):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 122880)
        self.assertGreater(len(seen), 1, "the fixture must have retried at least once")
        self.assertEqual(set(seen), {122880}, "a non-memory error changed the window: %s" % seen)
        self.assertEqual([l for l in self.logged if "CTX DOWNGRADE" in l], [])

    def test_a_HELD_window_is_asked_once_and_raised_as_unavailable(self):
        """A held window must be attempted exactly once at the original size, never shrunk, and raised as WindowUnavailable."""
        import chat_window
        seen = []

        def fake_once(host, model, messages, tools_list, ctx, keep_alive, timeout):
            seen.append(ctx)
            raise chat_window.WindowUnavailable("mothership is not available for qwen3.8:27b now (held: every "
                                                "capable factory is unavailable right now: mothership (model fit: "
                                                "no fresh card-memory report))")

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
             mock.patch.object(ollama_client.time, "sleep", lambda *_: None), \
             self.assertRaises(ollama_client.ChatError) as cm:
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 125952)
        self.assertEqual(seen, [125952], "a held window was asked again: %s" % seen)
        self.assertTrue(cm.exception.unavailable)
        self.assertEqual([l for l in self.logged if "CTX DOWNGRADE" in l], [])
        self.assertIsInstance(chat_window.WindowUnavailable("x"), httpx.ConnectError,
                              "every existing ConnectError failover must still catch it")


if __name__ == "__main__":
    unittest.main()
