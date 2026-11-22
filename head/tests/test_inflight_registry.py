"""These tests verify that in-flight registration occurs internally within the Ollama request boundary, ensuring visibility on the dashboard for all paths reaching Ollama."""
import re
import pathlib
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import cancellation
import embeddings
import ollama_client

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


class _FakeStream:
    """Minimal httpx.stream() stand-in: yields one done chunk, or raises inside the body."""

    def __init__(self, lines=None, raise_exc=None, hook=None):
        self.lines = lines if lines is not None else ['{"message":{"content":"hi"},"done":true,"model":"m"}']
        self.raise_exc = raise_exc
        self.hook = hook
        self.status_code = 200

    def __enter__(self):
        if self.hook:
            self.hook()
        if self.raise_exc:
            raise self.raise_exc
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        return iter(self.lines)


class TrackTests(unittest.TestCase):

    def setUp(self):
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INFLIGHT.clear()

    def test_track_registers_for_the_duration_and_clears_on_exception(self):
        with ollama_client.track("http://x:11434", "m", purpose="p", user="u"):
            snap = ollama_client.inflight_snapshot()
            self.assertEqual(len(snap["rows"]), 1)
            self.assertEqual(snap["rows"][0]["purpose"], "p")
            self.assertEqual(snap["by_host"]["http://x:11434"]["calls"], 1)
        self.assertEqual(ollama_client.inflight_snapshot()["rows"], [])
        with self.assertRaises(RuntimeError):
            with ollama_client.track("http://x:11434", "m"):
                raise RuntimeError("boom")
        self.assertEqual(ollama_client.inflight_snapshot()["rows"], [], "a failing request must not leak a row")

    def test_chat_once_is_registered_while_the_stream_is_open(self):
        seen = {}

        def hook():
            seen["during"] = ollama_client.inflight_snapshot()["rows"]
        with mock.patch.object(ollama_client, "_open_chat_stream", return_value=_FakeStream(hook=hook)):
            with ollama_client.call_context(purpose="eval_step coding-01 r1", user="eval:abc"):
                out = ollama_client._chat_once("http://h:11434", "qwen", [], [], 8192, "5m")
        self.assertEqual(out["message"]["content"], "hi")
        self.assertEqual(len(seen["during"]), 1)
        self.assertEqual(seen["during"][0]["purpose"], "eval_step coding-01 r1")
        self.assertEqual(seen["during"][0]["user"], "eval:abc")
        self.assertEqual(seen["during"][0]["host"], "http://h:11434")
        self.assertEqual(ollama_client.inflight_snapshot()["rows"], [])

    def test_chat_once_clears_when_the_stream_raises(self):
        with mock.patch.object(ollama_client, "_open_chat_stream", return_value=_FakeStream(raise_exc=ValueError("cut"))):
            with self.assertRaises(ValueError):
                ollama_client._chat_once("http://h:11434", "qwen", [], [], 8192, "5m")
        self.assertEqual(ollama_client.inflight_snapshot()["rows"], [])

    def test_call_context_restores_the_previous_label(self):
        with ollama_client.call_context(purpose="outer"):
            with ollama_client.call_context(purpose="inner"):
                self.assertEqual(ollama_client._CALL_CONTEXT.purpose, "inner")
            self.assertEqual(ollama_client._CALL_CONTEXT.purpose, "outer")

    def test_embed_is_registered(self):
        seen = {}

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"embeddings": [[0.1]]}

        def post(url, json=None, timeout=None):
            seen["during"] = ollama_client.inflight_snapshot()["rows"]
            return R()
        with mock.patch.object(embeddings, "_hosts_to_try", return_value=["http://e:11434"]), \
                mock.patch.object(embeddings.httpx, "post", side_effect=post):
            embeddings._embed(["a"])
        self.assertEqual(len(seen["during"]), 1)
        self.assertEqual(seen["during"][0]["kind"], "embed")
        self.assertEqual(ollama_client.inflight_snapshot()["rows"], [])


class TileTests(unittest.TestCase):

    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INFLIGHT.clear()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INFLIGHT.clear()

    def test_thinking_tile_is_idle_only_when_both_registries_are_empty(self):
        self.assertEqual(self.client.get("/hai/thinking").json()["state"], "idle")
        with ollama_client.track(app_module.config.BRAIN_HOST, "qwen3.5:9b", purpose="eval_step coding-02 r1", user="eval:c1"):
            d = self.client.get("/hai/thinking").json()
        self.assertEqual(d["state"], "busy")
        self.assertEqual(d["active"], 1)
        self.assertIn("eval_step coding-02", d["stage"])
        self.assertIn("bighonker", d["stage"])
        self.assertEqual(d["who"], "eval:c1")
        self.assertEqual(self.client.get("/hai/thinking").json()["state"], "idle")

    def test_inflight_endpoint_names_the_box_and_the_llm_tile_counts_it(self):
        with ollama_client.track(app_module.config.HEAVY_HOST, "qwen3.6:27b", purpose="absurd deep-think", user="ClaudeSupervisor"):
            d = self.client.get("/hai/inflight").json()
            self.assertEqual(d["active"], 1)
            self.assertEqual(d["rows"][0]["host"], "mothership")
            self.assertTrue(d["tile"]["mothership"].startswith("1 ·"))
            self.assertEqual(d["tile"]["bighonker"], "0")
            with mock.patch.object(ollama_client, "model_state", return_value=("qwen3.6:27b", "hot")):
                llm = self.client.get("/llm/mothership").json()
                bh = self.client.get("/llm/bighonker").json()
        self.assertEqual(llm["inflight_n"], 1)
        self.assertIn("absurd", llm["inflight"])
        self.assertEqual(bh["inflight"], "0")

    def test_query_purpose_labels_machine_paths_and_keeps_a_more_specific_outer_label(self):
        q = app_module.Q(text="x", user="u", ephemeral=True)
        object.__setattr__(q, "absurd_max_seconds", 60)
        self.assertEqual(app_module._query_purpose(q), "absurd deep-think")
        with ollama_client.call_context(purpose="absurd abr-01"):
            self.assertIsNone(app_module._query_purpose(q), "an outer, more specific label must win")
        self.assertEqual(app_module._query_purpose(app_module.Q(text="x", user="u")), "chat")


class EveryOllamaCallIsTrackedTests(unittest.TestCase):
    """Coverage pin. Registration lives inside ollama_client._chat_once, so chat callers cannot bypass it;
    the side doors (embeddings, VLM probes, unload) call httpx directly and must wrap the call in
    ollama_client.track(). This scans source for any Ollama API request outside a track() block: a
    source-text assertion, kept because an untracked call has NO observable behaviour in a unit test
    (it is exactly the blind spot), and comments are stripped before matching."""

    # Match all HTTP clients in use (httpx, urllib), not just one.
    CALL = re.compile(r"httpx\.(?:post|get|stream|request)\(|urllib\.request\.(?:Request|urlopen)\(|\b_ur\.(?:Request|urlopen)\(")
    API = re.compile(r"/api/(?:chat|generate|embed|embeddings)\b")

    @classmethod
    def _untracked(cls, root):
        offenders = []
        for py in sorted(root.glob("*.py")):
            lines = [l.split("#", 1)[0] for l in py.read_text(encoding="utf-8").splitlines()]
            for i, line in enumerate(lines):
                # check the next 4 lines: URL may be split across multiple arguments
                if not cls.CALL.search(line):
                    continue
                if not cls.API.search(line if "/api/" in line else "\n".join(lines[i:i + 4])):
                    continue
                if py.name == "ollama_client.py" and "_chat_once_raw" in "".join(lines[max(0, i - 40):i]):
                    continue          # the registered boundary itself
                # a urllib Request only names the URL; it is SENT by urlopen a few lines later, inside track()
                after = 12 if "Request(" in line else 0
                window = "\n".join(lines[max(0, i - 3):i + 1 + after])
                if "track(" not in window:
                    offenders.append("%s:%d %s" % (py.name, i + 1, line.strip()[:80]))
        return offenders

    def test_no_untracked_ollama_api_call(self):
        root = pathlib.Path(app_module.__file__).resolve().parent
        offenders = self._untracked(root)
        self.assertEqual(offenders, [], "Ollama API calls outside ollama_client.track():\n  " + "\n  ".join(offenders))

    def test_the_scan_sees_a_call_split_across_lines(self):
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "decoy.py").write_text('def f(host):\n    httpx.post(\n        f"{host}/api/chat",\n        json={})\n',
                                    encoding="utf-8")
        self.assertEqual(len(self._untracked(d)), 1, "the known-positive decoy must be flagged")

    def test_the_scan_sees_a_urllib_call(self):
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "decoy.py").write_text('def f(h):\n    req = urllib.request.Request(\n        f"{h}/api/chat",\n'
                                    '        data=b"{}")\n    return urllib.request.urlopen(req)\n', encoding="utf-8")
        self.assertEqual(len(self._untracked(d)), 1, "an untracked urllib call must be flagged")

    def test_the_proxy_registers_while_its_request_is_open(self):
        # the proxy builds its URL from a variable, so no source scan can see it: assert the behaviour
        import ollama_proxy
        from unittest.mock import patch
        seen = []

        class _Resp:
            status = 200

            def __enter__(self):
                seen.append(len(ollama_client.inflight_snapshot()["rows"]))
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"message": {"content": "x"}}'

        with patch.object(ollama_proxy, "choose_host", lambda **k: ("mothership", "test")), \
                patch.object(ollama_proxy, "_base_url", lambda h: "http://mothership.test:11434"), \
                patch.object(ollama_proxy.urllib.request, "urlopen", lambda *a, **k: _Resp()):
            before = len(ollama_client.inflight_snapshot()["rows"])
            ollama_proxy.forward("/api/chat", {"model": "m"})
        self.assertEqual(seen, [before + 1], "the forwarded request must be in the in-flight registry while open")
