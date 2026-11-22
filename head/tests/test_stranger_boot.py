"""A head with NO settings (no roster, no users, no lab addresses; only a model-server address that answers nothing) is
healthy, holds one device, says at once and in plain words that it cannot reach its model server and which setting
names it, and carries no lab literal in /health or that answer. Run in a subprocess so no setting of the suite's own
leaks in; the model-server address is a dead loopback port, so nothing leaves the machine.

The second test is 2.4 in software: the same head, with that one setting pointed at a model server that answers (a
fake one on loopback that speaks Ollama's wire format), answers the message FROM it. The live proof against a real
model server is run by hand, not here."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HEAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = re.compile(r"mothership|bighonker|softserve|sundae|rigby|192\.168\.|hawktopus|\balex\b", re.I)
SAID = "The one model server answered this."
PROBE = r"""
import json, time
from fastapi.testclient import TestClient
import app, config, scheduler
c = TestClient(app.app)
health = c.get("/health")
t0 = time.time()
answer = c.post("/query", json={"user": "test", "text": "hello, are you there?"})
print("RESULT " + json.dumps({"health_status": health.status_code, "health": health.json(),
                              "answer_status": answer.status_code, "answer": answer.json(),
                              "secs": round(time.time() - t0, 1), "devices": sorted(scheduler.CARDS),
                              "roster": config.ROSTER_SOURCE, "label": config.BRAIN_LABEL}))
"""
# A model server on loopback that answers like Ollama: it serves one model under any name it is asked for, streams a
# chat answer in Ollama's NDJSON chunks, and records every request (method, path, model) so the test can see where the
# head's calls went. It is started BEFORE the head is imported, at the address the head's one setting names.
FAKE = r"""
import json, os, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
SAID = %r
CALLS = []
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        CALLS.append(["GET", self.path, ""])
        if self.path == "/api/version":
            return self._send({"version": "0.40.5"})
        if self.path in ("/api/tags", "/api/ps"):
            return self._send({"models": []})
        return self._send({"error": "not found"}, 404)
    def do_POST(self):
        body = self._body()
        model = str(body.get("model") or "")
        CALLS.append(["POST", self.path, model])
        if self.path == "/api/show":
            return self._send({"capabilities": ["completion", "tools"], "model_info": {}, "details": {}})
        if self.path == "/api/chat":
            done = {"model": model, "done": True, "done_reason": "stop", "eval_count": 7, "prompt_eval_count": 20,
                    "message": {"role": "assistant", "content": ""}}
            if body.get("stream") is False:
                done["message"]["content"] = SAID
                return self._send(done)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for piece in (SAID[:12], SAID[12:]):
                self.wfile.write((json.dumps({"model": model, "done": False,
                                              "message": {"role": "assistant", "content": piece}}) + "\n").encode())
                self.wfile.flush()
            self.wfile.write((json.dumps(done) + "\n").encode())
            return
        return self._send({"error": "not found"}, 404)
server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["BRAIN_HOST"] = "http://127.0.0.1:%%d" %% server.server_address[1]
""" % (SAID,)
FAKE_RESULT = r"""
print("CALLS " + json.dumps({"calls": CALLS, "address": os.environ["BRAIN_HOST"]}))
"""


def _env(root, brain):
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": root, "PYTHONPATH": HEAD,
           "HAI_STATE_DIR": os.path.join(root, "state"), "DOCS_DIR": os.path.join(root, "docs"),
           "HAI_NOTES_DIR": os.path.join(root, "notes"), "USERCORPORA_DIR": os.path.join(root, "usercorpora"),
           "EMBED_ENABLED": "0", "HAI_TEST_ISOLATED": "1"}
    if brain:
        env["BRAIN_HOST"] = brain
    return env


def _run(code, env):
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=HEAD, timeout=300)
    found = {}
    for tag in ("RESULT", "CALLS"):
        line = [ln for ln in out.stdout.splitlines() if ln.startswith(tag + " ")]
        if line:
            found[tag] = json.loads(line[-1][len(tag) + 1:])
    return found, out


class AHeadWithNoSettings(unittest.TestCase):
    def test_is_healthy_says_what_to_fix_at_once_and_names_nothing_of_the_lab(self):
        found, out = _run(PROBE, _env(tempfile.mkdtemp(prefix="stranger-"), "http://127.0.0.1:9"))
        self.assertIn("RESULT", found, "the probe printed no result: %s" % (out.stderr[-1500:],))
        r = found["RESULT"]
        self.assertIsNone(r["roster"], "no roster file: the one-device roster")
        self.assertEqual(r["devices"], ["local"])
        self.assertEqual((r["health_status"], r["health"].get("ok")), (200, True))
        self.assertEqual(r["answer_status"], 200)
        msg = r["answer"].get("message") or ""
        self.assertIn("BRAIN_HOST", msg, "the answer names the setting to fix: %r" % msg)
        self.assertIn("127.0.0.1:9", msg)
        self.assertLess(r["secs"], 10, "a head with nothing to wake says so at once (it took %ss)" % r["secs"])
        for what in (json.dumps(r["health"]), msg):
            self.assertIsNone(LAB.search(what), "a lab literal reached a stranger: %r" % (LAB.search(what),))


class AHeadWithAModelServerAndNothingMeasured(unittest.TestCase):
    """The code names no model: a turn is placed from a measured menu or a pin. A head that reaches a live model server
    with neither says so, and how to get one, and never sends the server a chat with an empty model name."""

    def test_says_how_to_measure_and_sends_no_empty_model(self):
        found, out = _run(FAKE + PROBE + FAKE_RESULT, _env(tempfile.mkdtemp(prefix="stranger-one-"), None))
        self.assertIn("RESULT", found, "the probe printed no result: %s" % (out.stderr[-2500:],))
        self.assertIn("CALLS", found, "the fake printed no call log: %s" % (out.stderr[-1500:],))
        r, calls = found["RESULT"], found["CALLS"]["calls"]
        self.assertIsNone(r["roster"])
        self.assertEqual(r["devices"], ["local"])
        self.assertEqual((r["health_status"], r["health"].get("ok")), (200, True))
        self.assertEqual(r["answer_status"], 200)
        msg = r["answer"].get("message") or ""
        for words in ("no measured model", "measure-your-own-box", "HAI_POLICY_TABLE"):
            self.assertIn(words, msg, "the answer says how to get a model: %s" % json.dumps(r["answer"])[:800])
        self.assertEqual([c for c in calls if c[:2] == ["POST", "/api/chat"] and not c[2]], [],
                         "a chat with an empty model name reached the server: %r" % (calls,))
        self.assertLess(r["secs"], 30, "it says so at once (it took %ss)" % r["secs"])
        for what in (json.dumps(r["health"]), msg):
            self.assertIsNone(LAB.search(what), "a lab literal reached a stranger: %r" % (LAB.search(what),))


if __name__ == "__main__":
    unittest.main()
