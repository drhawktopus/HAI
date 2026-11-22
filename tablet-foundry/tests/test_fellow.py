"""FELLOW — the resident tablet agent (PROJECT #32).

Tests assert BEHAVIOUR, never source text. A test that greps for a call name fails when someone
improves the code and passes while the behaviour rots -- this lab has been bitten three times.

Three layers on purpose:
  * `_fellow_app` directly      -- the request logic, no socket
  * a REAL ThreadingHTTPServer  -- the seam. Unit tests do not look where producer and consumer
                                   must agree, and that is exactly where the last two silent
                                   failures lived (a separator that never matched, a JSON pattern
                                   that could not match in any state).
  * `fellow_dispatch` w/ a fake transport -- scheduling is the property under test, not HTTP.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as TF  # noqa: E402
import tablet_watch as TW  # noqa: E402

TOKEN = "test-token-do-not-ship"


def _reset():
    os.environ["TABLET_FELLOW_TOKEN"] = TOKEN
    os.environ["TABLET_FELLOW_NAME"] = "testnode"
    TF._FELLOW_STATS.update({"served": 0, "failed": 0, "busy": 0,
                             "last_error": "", "last_task": ""})
    TF._PAYLOAD_CACHE["key"] = None
    TF._PAYLOAD_CACHE["mod"] = None


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# A payload honouring the EXISTING contract: main(items_path, out_path). The module-level counter
# is how we prove the import happens once -- that is the entire point of a resident agent.
PAYLOAD_SRC = """
import json
LOADS = []
LOADS.append(1)
MARK = %r

def main(items_path, out_path):
    items = [l.strip() for l in open(items_path, encoding='utf-8') if l.strip()]
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'items': items, 'loads': len(LOADS), 'mark': MARK}, f)
"""

RAISING_PAYLOAD = """
def main(items_path, out_path):
    raise ValueError('this unit is bad')
"""


class Auth(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_missing_token_is_refused(self):
        code, obj = TF._fellow_app("GET", "/health", None, "")
        self.assertEqual(code, 401)
        self.assertFalse(obj["ok"])

    def test_wrong_token_is_refused(self):
        code, _ = TF._fellow_app("GET", "/health", None, "not-the-token")
        self.assertEqual(code, 401)

    def test_correct_token_is_accepted(self):
        code, obj = TF._fellow_app("GET", "/health", None, TOKEN)
        self.assertEqual(code, 200)
        self.assertTrue(obj["ok"])

    def test_agent_refuses_to_start_without_a_token(self):
        """The 'payload' task runs code the head names. No token must mean NO LISTENER, never
        an open one -- fail closed, and loudly."""
        os.environ.pop("TABLET_FELLOW_TOKEN", None)
        old = TF.FELLOW_TOKEN_PATH
        TF.FELLOW_TOKEN_PATH = os.path.join(os.path.dirname(__file__), "_no_such_token_file")
        try:
            self.assertEqual(TF.fellow_serve(port=0), 2)
        finally:
            TF.FELLOW_TOKEN_PATH = old
            os.environ["TABLET_FELLOW_TOKEN"] = TOKEN


class Tasks(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fellow_tmp")
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        for f in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, f))
            except Exception:
                pass

    def test_ping_round_trips(self):
        code, obj = TF._fellow_app("POST", "/work", {"task": "ping", "echo": "hi"}, TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(obj["result"]["pong"], "hi")

    def test_unknown_task_is_a_400_and_lists_what_exists(self):
        code, obj = TF._fellow_app("POST", "/work", {"task": "nope"}, TOKEN)
        self.assertEqual(code, 400)
        self.assertIn("ping", obj["tasks"])

    def test_payload_runs_and_returns_its_output(self):
        p = _write(os.path.join(self.tmp, "pay.py"), PAYLOAD_SRC % "v1")
        code, obj = TF._fellow_app("POST", "/work",
                                   {"task": "payload", "payload": p, "items": ["a", "b"]}, TOKEN)
        self.assertEqual(code, 200)
        self.assertTrue(obj["ok"], obj)
        self.assertEqual(obj["result"]["out"]["items"], ["a", "b"])

    def test_payload_is_imported_ONCE_across_calls(self):
        """THE WHOLE POINT OF THE PROJECT. If this fails, the agent is just a slower ssh."""
        p = _write(os.path.join(self.tmp, "pay.py"), PAYLOAD_SRC % "v1")
        req = {"task": "payload", "payload": p, "items": ["x"]}
        first = TF._fellow_app("POST", "/work", dict(req), TOKEN)[1]
        second = TF._fellow_app("POST", "/work", dict(req), TOKEN)[1]
        self.assertTrue(first["result"]["reloaded"])
        self.assertFalse(second["result"]["reloaded"])
        # module-level state survived -> the model would have survived too
        self.assertEqual(second["result"]["out"]["loads"], 1)

    def test_a_CHANGED_payload_is_re_imported(self):
        """Deployed is not running. A cache validated by existence alone silently serves the old
        code -- the fingerprint is what makes a redeploy take effect."""
        p = os.path.join(self.tmp, "pay.py")
        _write(p, PAYLOAD_SRC % "v1")
        req = {"task": "payload", "payload": p, "items": ["x"]}
        TF._fellow_app("POST", "/work", dict(req), TOKEN)
        time.sleep(0.01)
        _write(p, PAYLOAD_SRC % "version-two-longer")
        out = TF._fellow_app("POST", "/work", dict(req), TOKEN)[1]
        self.assertTrue(out["result"]["reloaded"])
        self.assertEqual(out["result"]["out"]["mark"], "version-two-longer")

    def test_a_payload_that_raises_is_a_UNIT_failure_not_a_node_failure(self):
        """Three fates. A bad input must not get this fellow blacklisted -- it returns 200 with
        ok:false, so the head keeps using a node that is working perfectly."""
        p = _write(os.path.join(self.tmp, "bad.py"), RAISING_PAYLOAD)
        code, obj = TF._fellow_app("POST", "/work",
                                   {"task": "payload", "payload": p, "items": ["x"]}, TOKEN)
        self.assertEqual(code, 200)
        self.assertFalse(obj["ok"])
        self.assertIn("this unit is bad", obj["error"])

    def test_scratch_files_are_removed_even_when_the_payload_raises(self):
        p = _write(os.path.join(self.tmp, "bad.py"), RAISING_PAYLOAD)
        TF._fellow_app("POST", "/work", {"task": "payload", "payload": p, "items": ["x"]}, TOKEN)
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith("fellow_")]
        self.assertEqual(leftovers, [], "scratch left behind: %s" % leftovers)

    def test_missing_payload_is_reported_not_crashed(self):
        code, obj = TF._fellow_app("POST", "/work",
                                   {"task": "payload", "payload": "/nope/x.py", "items": []}, TOKEN)
        self.assertEqual(code, 200)
        self.assertFalse(obj["ok"])
        self.assertIn("not staged", obj["error"])


class Busy(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_a_second_unit_gets_503_rather_than_queueing(self):
        """The head does the scheduling. A node that queues hides contention from the only
        component that could route around it."""
        TF._FELLOW_LOCK.acquire()
        try:
            code, obj = TF._fellow_app("POST", "/work", {"task": "ping"}, TOKEN)
        finally:
            TF._FELLOW_LOCK.release()
        self.assertEqual(code, 503)
        self.assertEqual(obj["error"], "busy")

    def test_the_lock_is_released_after_a_failing_task(self):
        TF._fellow_app("POST", "/work",
                       {"task": "payload", "payload": "/nope.py", "items": []}, TOKEN)
        self.assertFalse(TF._FELLOW_LOCK.locked(), "lock leaked on the error path")


class LiveSocket(unittest.TestCase):
    """THE SEAM. Producer and consumer must agree over a real connection, with real JSON, real
    headers and a real Bearer token. Everything above could pass while this fails."""

    @classmethod
    def setUpClass(cls):
        _reset()
        from http.server import ThreadingHTTPServer
        cls.port = 8739
        # build the server the same way fellow_serve does, via the module's own handler
        cls.thread = threading.Thread(
            target=lambda: TF.fellow_serve(port=cls.port, token=TOKEN), daemon=True)
        cls.thread.start()
        for _ in range(100):                       # exit on a VERDICT, not on a clock
            try:
                urllib.request.urlopen(
                    urllib.request.Request("http://127.0.0.1:%d/health" % cls.port,
                                           headers={"Authorization": "Bearer %s" % TOKEN}),
                    timeout=1)
                return
            except urllib.error.HTTPError:
                return                             # answered, even if a 4xx -- it is up
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("fellow did not come up on :%d" % cls.port)

    def _get(self, path, token=TOKEN):
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   headers={"Authorization": "Bearer %s" % token})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_health_over_a_real_socket(self):
        code, obj = self._get("/health")
        self.assertEqual(code, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["role"], "fellow")
        self.assertIn("payload", obj["tasks"])

    def test_a_real_request_with_a_bad_token_is_refused_over_the_wire(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/health", token="wrong")
        self.assertEqual(cm.exception.code, 401)

    def test_work_over_a_real_socket(self):
        body = json.dumps({"task": "ping", "echo": "seam"}).encode()
        r = urllib.request.Request("http://127.0.0.1:%d/work" % self.port, data=body,
                                   headers={"Authorization": "Bearer %s" % TOKEN,
                                            "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            obj = json.loads(resp.read().decode())
        self.assertEqual(obj["result"]["pong"], "seam")

    def test_unknown_path_is_404_not_a_hang(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/nope")
        self.assertEqual(cm.exception.code, 404)


class Dispatch(unittest.TestCase):
    """Scheduling behaviour, with the transport faked. HTTP is proven above; this is about who
    gets which unit and what happens when a node misbehaves."""

    def setUp(self):
        _reset()
        self.calls = []
        self._health = TF.fellow_health
        self._call = TF.fellow_call

    def tearDown(self):
        TF.fellow_health = self._health
        TF.fellow_call = self._call

    def _fake(self, live, behaviour):
        TF.fellow_health = lambda hosts=None, timeout=4.0: {
            h: {"host": h, "alive": h in live, "tasks": ["ping", "payload"],
                "capabilities": ["task:ping", "task:payload", "arch:aarch64",
                                 "runtime:64bit"]}
            for h in ["a", "b", "c"]}
        TF.fellow_call = behaviour

    def test_results_come_back_in_INPUT_order(self):
        def call(host, path, body=None, timeout=None, token=None):
            self.calls.append((host, body["unit"]))
            return True, {"ok": True, "result": body["unit"] * 10}
        self._fake(["a", "b"], call)
        out = TF.fellow_dispatch([1, 2, 3, 4, 5], task="ping")
        self.assertEqual([r["result"] for r in out], [10, 20, 30, 40, 50])

    def test_every_unit_gets_a_result_even_with_no_live_node(self):
        """A short list is indistinguishable from work that succeeded. Absent must be explicit."""
        self._fake([], lambda *a, **k: (False, {"ok": False}))
        out = TF.fellow_dispatch([1, 2, 3])
        self.assertEqual(len(out), 3)
        self.assertTrue(all(not r["ok"] for r in out))
        self.assertTrue(all("no fellow reachable" in r["error"] for r in out))

    def test_a_dead_node_does_not_strand_its_units(self):
        """Work-stealing, not a pre-split: if 'a' is dead, 'b' must finish everything."""
        def call(host, path, body=None, timeout=None, token=None):
            if host == "a":
                return False, {"ok": False, "error": "ConnectionRefused"}
            return True, {"ok": True, "result": body["unit"]}
        self._fake(["a", "b"], call)
        out = TF.fellow_dispatch([1, 2, 3, 4], task="ping")
        self.assertTrue(all(r["ok"] for r in out), out)
        self.assertTrue(all(r["host"] == "b" for r in out))

    def test_a_slow_node_simply_takes_FEWER_units(self):
        """The property a fixed split cannot give you. 'slow' must not hold a fixed band."""
        def call(host, path, body=None, timeout=None, token=None):
            if host == "slow":
                time.sleep(0.05)
            return True, {"ok": True, "result": body["unit"]}
        TF.fellow_health = lambda hosts=None, timeout=4.0: {
            "fast": {"host": "fast", "alive": True, "tasks": ["ping"],
                     "capabilities": ["task:ping"]},
            "slow": {"host": "slow", "alive": True, "tasks": ["ping"],
                     "capabilities": ["task:ping"]}}
        TF.fellow_call = call
        out = TF.fellow_dispatch(list(range(20)), task="ping")
        fast = sum(1 for r in out if r["host"] == "fast")
        self.assertEqual(len(out), 20)
        self.assertGreater(fast, 12, "fast node should have stolen most of the work, got %d" % fast)

    def test_every_unit_is_accounted_for_when_all_nodes_die_MID_RUN(self):
        """When all nodes fail, every unit must return as an explicit failure.

        The result list must contain exactly 5 items (no vanishing units), all marked
        with `ok: False` and a specific error message. This prevents silent drops that
        previously slipped through the suite."""
        self._fake(["a", "b"], lambda *a, **k: (False, {"ok": False, "error": "ConnRefused"}))
        out = TF.fellow_dispatch([1, 2, 3, 4, 5], task="ping")
        self.assertEqual(len(out), 5, "units vanished from the result list")
        self.assertTrue(all(r is not None and not r["ok"] for r in out), out)
        self.assertTrue(any("no fellow completed" in r["error"] for r in out), out)
        self.assertEqual([r["index"] for r in out], [0, 1, 2, 3, 4])

    def test_a_unit_requeued_by_a_DYING_worker_is_still_picked_up(self):
        """The race the backstop was hiding: 'a' fails and requeues as it exits. If 'b' had
        already drained and left, that unit would be stranded and reported failed while a
        healthy node sat idle. Nobody should lose work to a scheduling gap."""
        seen = {"a": 0}

        def call(host, path, body=None, timeout=None, token=None):
            if host == "a":
                seen["a"] += 1
                time.sleep(0.02)                    # lose the race to b on purpose
                return False, {"ok": False, "error": "ConnRefused"}
            time.sleep(0.001)
            return True, {"ok": True, "result": body["unit"]}
        self._fake(["a", "b"], call)
        out = TF.fellow_dispatch(list(range(6)), task="ping")
        self.assertEqual(len(out), 6)
        self.assertTrue(all(r["ok"] for r in out),
                        "a healthy node was available but a unit was stranded: %s" % out)
        self.assertGreaterEqual(seen["a"], 1, "test did not actually exercise the dying worker")

    def test_a_busy_node_gets_the_unit_retried_elsewhere(self):
        state = {"busy": True}

        def call(host, path, body=None, timeout=None, token=None):
            if host == "a" and state["busy"]:
                return True, {"ok": False, "error": "busy"}
            return True, {"ok": True, "result": body["unit"]}
        self._fake(["a", "b"], call)
        out = TF.fellow_dispatch([1, 2], task="ping")
        self.assertTrue(all(r["ok"] for r in out), out)


    def test_required_arch_excludes_an_incompatible_normal_member(self):
        calls = []
        TF.fellow_health = lambda hosts=None, timeout=4.0: {
            "smeagol": {"alive": True, "tasks": ["payload", "ping"],
                        "capabilities": ["task:payload", "task:ping", "arch:armv7l",
                                         "runtime:32bit"]},
            "gandalf": {"alive": True, "tasks": ["payload", "ping"],
                        "capabilities": ["task:payload", "task:ping", "arch:aarch64",
                                         "runtime:64bit"]},
        }
        def call(host, path, body=None, timeout=None, token=None):
            calls.append(host)
            return True, {"ok": True, "result": body["unit"]}
        TF.fellow_call = call
        out = TF.fellow_dispatch(["unit"], task="payload",
                                 extra={"requires": ["arch:aarch64"]})
        self.assertTrue(out[0]["ok"], out)
        self.assertEqual(calls, ["gandalf"])



class HealthReporting(unittest.TestCase):
    def setUp(self):
        _reset()
        self._call = TF.fellow_call

    def tearDown(self):
        TF.fellow_call = self._call

    def test_an_unreachable_node_is_PRESENT_and_false_never_omitted(self):
        """A node missing from a health map reads as 'not asked'. Absence must be measured."""
        TF.fellow_call = lambda h, p, body=None, timeout=None, token=None: (
            (True, {"ok": True}) if h == "gandalf" else (False, {"ok": False, "error": "refused"}))
        out = TF.fellow_health(["gandalf", "frodo"])
        self.assertEqual(set(out), {"gandalf", "frodo"})
        self.assertTrue(out["gandalf"]["alive"])
        self.assertFalse(out["frodo"]["alive"])

    def test_the_REGISTRY_name_wins_over_the_devices_self_report(self):
        """A self-report must not relabel the map. Android answered 'localhost' on every node,
        which would have made every entry claim to be the same host."""
        TF.fellow_call = lambda h, p, body=None, timeout=None, token=None: (
            True, {"ok": True, "host": "localhost"})
        out = TF.fellow_health(["merry"])
        self.assertEqual(out["merry"]["host"], "merry")
        self.assertTrue(out["merry"]["alive"])
        self.assertNotIn("identity_mismatch", out["merry"])

    def test_a_device_answering_to_the_WRONG_NAME_is_flagged(self):
        """If the box at merry's address says it is pippin, DHCP has shuffled and work is landing
        on the wrong tablet. That is a finding, not something to paper over."""
        TF.fellow_call = lambda h, p, body=None, timeout=None, token=None: (
            True, {"ok": True, "host": "pippin"})
        out = TF.fellow_health(["merry"])
        self.assertEqual(out["merry"]["host"], "merry")
        self.assertIn("identity_mismatch", out["merry"])
        self.assertIn("pippin", out["merry"]["identity_mismatch"])

    def test_wakelock_returns_None_when_it_cannot_tell(self):
        """'Could not tell' must never render as a definite state. None, not False."""
        old = TF.sh
        TF.sh = lambda cmd, timeout=10: (127, "termux-wake-lock: not found")
        try:
            held, why = TF._fellow_wakelock_state()
        finally:
            TF.sh = old
        self.assertIsNone(held)
        self.assertIn("could not confirm", why)


class SchedulerGate(unittest.TestCase):
    def test_head_side_refuses_to_run_without_a_scheduler(self):
        """Tolerant in the agent must not mean silently headless on the head."""
        old_s, old_e = TF.S, TF._SCHED_ERR
        TF.S, TF._SCHED_ERR = None, ImportError("no module named scheduler")
        try:
            with self.assertRaises(RuntimeError) as cm:
                TF._require_scheduler()
            self.assertIn("scheduler unavailable", str(cm.exception))
        finally:
            TF.S, TF._SCHED_ERR = old_s, old_e


class GuardsAreActuallyLoadBearing(unittest.TestCase):
    """CONTROL CLASS. Each test breaks a guard and proves the suite would notice. A guard nothing
    exercises is indistinguishable from no guard, and 185 green tests once sat beside a detector
    that was completely blind."""

    def setUp(self):
        _reset()

    def test_removing_the_auth_check_would_be_caught(self):
        code, _ = TF._fellow_app("GET", "/health", None, "definitely-wrong")
        self.assertNotEqual(code, 200, "auth check is not load-bearing -- a bad token got in")

    def test_removing_the_busy_gate_would_be_caught(self):
        TF._FELLOW_LOCK.acquire()
        try:
            code, _ = TF._fellow_app("POST", "/work", {"task": "ping"}, TOKEN)
        finally:
            TF._FELLOW_LOCK.release()
        self.assertNotEqual(code, 200, "busy gate is not load-bearing -- work ran concurrently")

    def test_the_payload_fingerprint_is_not_path_only(self):
        """If the cache key were the path alone, a rewritten file would NOT reload and this
        would fail -- which is the point."""
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fellow_tmp")
        os.makedirs(tmp, exist_ok=True)
        p = os.path.join(tmp, "fp.py")
        _write(p, PAYLOAD_SRC % "one")
        TF._fellow_load_payload(p)
        time.sleep(0.01)
        _write(p, PAYLOAD_SRC % "two-is-longer")
        _mod, reloaded = TF._fellow_load_payload(p)
        self.assertTrue(reloaded, "fingerprint is path-only -- a redeploy would run stale code")
        try:
            os.remove(p)
        except Exception:
            pass


class DetectorSpeaksFrigatesProtocol(unittest.TestCase):
    """The detector offload (SCOPE 7f).

        The multipart body is built with `requests` to match the actual library used by
        frigate/detectors/plugins/deepstack.py, guarding against serialization mismatches.
    """

    def _body(self, jpeg=b"\xff\xd8notreallyajpeg", api_key="k"):
        """A real multipart body, produced exactly the way Frigate produces it."""
        import requests

        req = requests.Request("POST", "http://x/v1/vision/detection",
                               data={"api_key": api_key},
                               files={"image": jpeg}).prepare()
        return req.body, req.headers["Content-Type"]

    def test_multipart_parser_agrees_with_the_library_frigate_actually_uses(self):
        jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\xff\xd9"
        body, ctype = self._body(jpeg)
        got = TF._detect_multipart_image(body, ctype)
        self.assertEqual(got, jpeg,
                         "parser and Frigate's encoder disagree -- every frame would be rejected")

    def test_a_body_with_no_image_part_is_not_silently_empty(self):
        import requests
        req = requests.Request("POST", "http://x", data={"api_key": "k"}).prepare()
        ctype = req.headers.get("Content-Type", "")
        self.assertIsNone(TF._detect_multipart_image(req.body or b"", ctype))

    def test_voc_names_are_translated_to_the_coco_names_frigate_carries(self):
        """`sofa` and `motorbike` are not COCO names. deepstack.py `break`s on an unknown label, so
        an untranslated one does not just vanish -- it discards every prediction behind it."""
        self.assertEqual(TF.VOC_TO_COCO["sofa"], "couch")
        self.assertEqual(TF.VOC_TO_COCO["motorbike"], "motorcycle")
        self.assertEqual(TF.VOC_TO_COCO["pottedplant"], "potted plant")

    def test_predictions_come_back_descending_and_filtered(self):
        """The C++ side sorts; this asserts the PROPERTY end to end through _detect_predict, with
        the resident process faked so the test needs no aarch64 binary."""
        fake = {"predictions": [
            {"label": "person", "confidence": 0.40, "x_min": 1, "y_min": 1, "x_max": 2, "y_max": 2},
            {"label": "cat", "confidence": 0.95, "x_min": 3, "y_min": 3, "x_max": 4, "y_max": 4},
            {"label": "chair", "confidence": 0.99, "x_min": 5, "y_min": 5, "x_max": 6, "y_max": 6},
        ]}
        # the binary emits descending order; emulate that faithfully
        fake["predictions"].sort(key=lambda d: -d["confidence"])
        orig = TF._detect_infer
        TF._detect_infer = lambda _raw: fake
        try:
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (TF.DETECT_W, TF.DETECT_H), (10, 20, 30)).save(buf, format="JPEG")
            preds = TF._detect_predict(buf.getvalue())
        finally:
            TF._detect_infer = orig
        labels = [p["label"] for p in preds]
        self.assertEqual(labels, ["cat", "person"],
                         "chair is not in the tracked set and must be dropped, cat must lead")
        scores = [p["confidence"] for p in preds]
        self.assertEqual(scores, sorted(scores, reverse=True), "ordering lost -- Frigate truncates")

    def test_boxes_are_rescaled_to_the_frame_frigate_actually_sent(self):
        """Frigate overrides the per-detector model size, so frames arrive at 320 while the net
        runs at 300. deepstack.py divides boxes by the size IT sent, so unscaled 300-space
        coordinates would shift every detection ~6% and drift a cat out of the couch zone -- with
        every number still looking entirely plausible."""
        fake = {"predictions": [{"label": "cat", "confidence": 0.9,
                                 "x_min": 0.0, "y_min": 0.0,
                                 "x_max": float(TF.DETECT_W), "y_max": float(TF.DETECT_H)}]}
        orig = TF._detect_infer
        TF._detect_infer = lambda _raw: fake
        try:
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (320, 320), (5, 5, 5)).save(buf, format="JPEG")
            preds = TF._detect_predict(buf.getvalue())
        finally:
            TF._detect_infer = orig
        self.assertEqual(len(preds), 1)
        # a full-frame box must come back as the FULL 320 frame, not 300 of it
        self.assertAlmostEqual(preds[0]["x_max"], 320.0, places=1)
        self.assertAlmostEqual(preds[0]["y_max"], 320.0, places=1)

    def test_a_broken_detector_returns_503_and_never_an_empty_room(self):
        """The single most dangerous failure mode here. A 200 with `predictions: []` is what an
        empty room looks like, so a dead tablet would read to Frigate as a quiet house forever."""
        body, ctype = self._body()
        orig = TF._detect_predict

        def boom(_jpeg):
            raise RuntimeError("resident detector died")

        TF._detect_predict = boom
        try:
            code, obj = TF._detect_app("POST", "/v1/vision/detection", body, ctype)
        finally:
            TF._detect_predict = orig
        self.assertEqual(code, 503, "a failure answered 200 is indistinguishable from an empty room")
        self.assertFalse(obj.get("success"))

    def test_availability_is_detected_not_declared(self):
        """A node advertises what it can actually do. Pointed at a path that does not exist, it
        must report unavailable rather than start a listener that 503s every frame."""
        orig = TF.DETECT_BIN
        TF.DETECT_BIN = os.path.join(os.path.dirname(__file__), "_no_such_detector_binary")
        try:
            ok, why = TF.detect_available()
        finally:
            TF.DETECT_BIN = orig
        self.assertFalse(ok)
        self.assertIn("detector binary", why)

    def test_selftest_fails_loudly_when_the_control_frame_is_absent(self):
        """No control means no way to tell a broken detector from a genuinely empty scene, so the
        decoy must refuse rather than report a cheerful pass."""
        orig = TF.DETECT_CONTROL
        TF.DETECT_CONTROL = os.path.join(os.path.dirname(__file__), "_no_such_control.jpg")
        try:
            r = TF.detect_selftest()
        finally:
            TF.DETECT_CONTROL = orig
        self.assertFalse(r["ok"])
        self.assertIn("control", r["error"])

    def test_selftest_refuses_when_it_cannot_say_what_the_control_contains(self):
        """A control frame with no stated expectation is not a control -- it is just a picture.

        The first version of this defaulted the expectation to "cat" in an env var, so replacing the
        image left the selftest asserting something about a frame that no longer existed."""
        here = os.path.dirname(__file__)
        img = os.path.join(here, "_tmp_control.jpg")
        from PIL import Image
        Image.new("RGB", (TF.DETECT_W, TF.DETECT_H), (0, 0, 0)).save(img)
        o_img, o_exp = TF.DETECT_CONTROL, TF.DETECT_CONTROL_EXPECT_FILE
        TF.DETECT_CONTROL = img
        TF.DETECT_CONTROL_EXPECT_FILE = os.path.join(here, "_no_such_sidecar.expect")
        try:
            r = TF.detect_selftest()
        finally:
            TF.DETECT_CONTROL, TF.DETECT_CONTROL_EXPECT_FILE = o_img, o_exp
            try:
                os.remove(img)
            except Exception:
                pass
        self.assertFalse(r["ok"])
        self.assertIn("expectation", r["error"])

    def test_unknown_detect_action_is_refused_rather_than_silently_status(self):
        with self.assertRaises(RuntimeError):
            TF._fellow_detect_task({"action": "definitely-not-a-real-action"})

    def test_the_detect_task_is_registered_so_tolkien_can_actually_reach_it(self):
        self.assertIn("detect", TF._FELLOW_HANDLERS)

    def test_staging_does_not_live_in_the_docker_build_context(self):
        """22 MB of binary and weights parked beside app.py would ride into every image build."""
        import inspect
        src = inspect.getsource(TF.detect_install)
        default = TF.detect_install.__doc__ and True
        self.assertIn("TABLET_DETECT_STAGE", src)
        self.assertTrue(default)
        build_ctx = os.path.dirname(os.path.dirname(os.path.abspath(TF.__file__)))
        stray = os.path.join(os.path.dirname(os.path.abspath(TF.__file__)), "detect-staging")
        self.assertFalse(os.path.exists(stray),
                         "detector staging is inside the docker build context: %s" % stray)
        del build_ctx


class NodeHealthAgentSha(unittest.TestCase):
    """NodeHealthAgentSha tests that the `sha:` prefix prevents hex hashes consisting entirely
    of digits from being misparsed as whitelist counts."""

    def setUp(self):
        _reset()
        self._ssh, self._req, self._ip = TF._ssh, TF._require_scheduler, TF.node_ip
        TF.node_ip = lambda host, port, refresh=False: ("10.0.0.71", "wifi")   # no real route probe

        class _S:
            @staticmethod
            def tablet_alive(host):
                return True
        TF._require_scheduler = lambda: _S

    def tearDown(self):
        TF._ssh, TF._require_scheduler, TF.node_ip = self._ssh, self._req, self._ip

    def test_all_digit_hash_is_not_read_as_the_whitelist_count(self):
        TF._ssh = lambda host, cmd, timeout=0: (0, "1\nsha:1234567890123456\n")
        h = TF.node_health("gandalf")
        self.assertEqual(h["agent_sha"], "1234567890123456")
        self.assertTrue(h["whitelisted"])
        self.assertTrue(h["healthy"])

    def test_node_that_cannot_hash_reports_none_and_stays_gradeable(self):
        TF._ssh = lambda host, cmd, timeout=0: (0, "1\nsha:\n")
        h = TF.node_health("gandalf")
        self.assertIsNone(h["agent_sha"])
        self.assertTrue(h["healthy"])

    def test_unreachable_node_still_carries_the_key(self):
        class _Dead:
            @staticmethod
            def tablet_alive(host):
                return False
        TF._require_scheduler = lambda: _Dead
        h = TF.node_health("gandalf")
        self.assertIn("agent_sha", h)
        self.assertIsNone(h["agent_sha"])


class NodeHealthWireFirst(unittest.TestCase):
    """Wire is the primary health check; a node with broken WiFi but working wire is considered alive."""

    def setUp(self):
        self._ssh, self._req, self._ip = TF._ssh, TF._require_scheduler, TF.node_ip

        class _WifiDead:
            @staticmethod
            def tablet_alive(host):
                return False
        TF._require_scheduler = lambda: _WifiDead
        TF._ssh = lambda host, cmd, timeout=0: (0, "1\nsha:abc\n")

    def tearDown(self):
        TF._ssh, TF._require_scheduler, TF.node_ip = self._ssh, self._req, self._ip

    def test_a_node_answering_on_the_wire_is_alive_with_its_wifi_down(self):
        TF.node_ip = lambda host, port, refresh=False: ("192.0.2.13", "wire")
        h = TF.node_health("legolas")
        self.assertTrue(h["alive"])
        self.assertTrue(h["healthy"])

    def test_a_node_dead_on_both_paths_is_still_fail_closed(self):
        TF.node_ip = lambda host, port, refresh=False: ("10.0.0.78", "wifi")
        h = TF.node_health("legolas")
        self.assertFalse(h["alive"])
        self.assertIn("unreachable", h["why"])


class AdbTargetsWireThenWifi(unittest.TestCase):
    """adb takes the USB wire first like every other path; WiFi is its backup when the wire does not answer."""

    def setUp(self):
        self._sh, self._req, self._map = TF.sh, TF._require_scheduler, dict(TF._ADB_MAP)
        TF._ADB_MAP.clear()

        class _S:
            TABLETS = {"legolas": {}}
        TF._require_scheduler = lambda: _S

    def tearDown(self):
        TF.sh, TF._require_scheduler = self._sh, self._req
        TF._ADB_MAP.clear()
        TF._ADB_MAP.update(self._map)

    def _run(self, wire_up):
        tried = []

        def sh(cmd, timeout=0):
            if " connect " in cmd:
                tgt = cmd.split(" connect ")[1]
                tried.append(tgt)
                ok = wire_up or not TF.on_wire(tgt.split(":")[0])   # the wire answers only when wire_up
                return 0, ("connected to %s" % tgt) if ok else ("failed to connect to %s" % tgt)
            return 0, "List of devices attached\n"
        TF.sh = sh
        TF._ADB_MAP_TS = 0.0
        return TF._adb_targets(ttl=0), tried

    def test_the_wire_is_tried_first_and_kept_when_it_answers(self):
        # the wire subnet is a setting (TABLET_WIRE_PREFIX); this one sets a documentation subnet
        with mock.patch.object(TF, "WIRE_PREFIX", "192.0.2."):
            wire = "192.0.2.%d:5555" % TF.WIRE_OCTET["legolas"]
            m, tried = self._run(wire_up=True)
        self.assertEqual(tried, [wire])
        self.assertEqual(m["legolas"], wire)

    def test_wifi_is_the_backup_when_the_wire_does_not_answer(self):
        # the LAN's DNS suffix is a setting (TABLET_DNS_SUFFIX); this one names it, so the WiFi target carries it
        saved, TF.TABLET_DNS_SUFFIX = TF.TABLET_DNS_SUFFIX, ".lan"
        try:
            with mock.patch.object(TF, "WIRE_PREFIX", "192.0.2."):
                m, tried = self._run(wire_up=False)
        finally:
            TF.TABLET_DNS_SUFFIX = saved
        self.assertEqual(tried, ["192.0.2.%d:5555" % TF.WIRE_OCTET["legolas"], "legolas.lan:5555"])
        self.assertEqual(m["legolas"], "legolas.lan:5555")

    def test_no_wire_subnet_means_wifi_only(self):
        with mock.patch.object(TF, "WIRE_PREFIX", ""):
            m, tried = self._run(wire_up=True)
        self.assertEqual(tried, ["legolas%s:5555" % TF.TABLET_DNS_SUFFIX])
        self.assertEqual(m["legolas"], "legolas%s:5555" % TF.TABLET_DNS_SUFFIX)


class FleetUnreachableDebounce(unittest.TestCase):
    """Two gates protect the fleet-unreachable DM. (1) DEBOUNCE: a one-poll WiFi zero is the
    re-association blip, so no DM until it survives UNREACH_CONFIRM consecutive polls -- the exact
    false alarm the deployed watcher sent (0/7 one poll, 7/7 the next, ~once a day). (2) SECOND PATH:
    WiFi is only one transport, so before DMing it confirms over the WiFi-independent USB segment
    (via rigby) and stays silent if the tablets are alive on the wire."""

    NODES = ("frodo", "sam", "legolas", "merry", "pippin", "gandalf", "aragorn")

    def setUp(self):
        self.dms = []
        self.tmp = tempfile.mkdtemp()
        hb = os.path.join(self.tmp, "hb.json")
        _write(hb, json.dumps({"ts": time.time(), "phase": "idle"}))
        TW.HEARTBEAT = hb
        TW.CURRENT = os.path.join(self.tmp, "no_current.json")   # no active order -> fleet branch
        TW.STAND_DOWN = os.path.join(self.tmp, "no_standdown")
        TW.notify_desk = lambda msg: self.dms.append(msg)
        TW._alerted.clear()
        # Default: USB also dead (truly unreachable). Individual tests override to exercise the paths.
        TW.usb_reach_via_rigby = lambda: set()
        self.all_dead = {n: {"alive": False, "healthy": False} for n in self.NODES}
        self.all_up = {n: {"alive": True, "healthy": True} for n in self.NODES}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patrol(self, health, fleet, prog):
        TW.fleet_health = lambda: health
        return TW.patrol(prog, fleet)

    def _drive_to_confirm(self, fleet=None, prog=None):
        """Feed UNREACH_CONFIRM consecutive all-dead-WiFi polls; return the last detail."""
        fleet, prog = fleet or TW.FleetReach(), prog or TW.Progress()
        detail = None
        for _ in range(TW.UNREACH_CONFIRM):
            _, detail = self._patrol(self.all_dead, fleet, prog)
        return detail

    def test_single_zero_poll_does_not_DM(self):
        self._patrol(self.all_dead, TW.FleetReach(), TW.Progress())
        self.assertEqual(self.dms, [], "a one-poll fleet-wide zero must not DM")

    def test_below_threshold_never_calls_USB_or_DMs(self):
        called = []
        TW.usb_reach_via_rigby = lambda: called.append(1) or set()
        n = TW.UNREACH_CONFIRM
        fleet, prog = TW.FleetReach(), TW.Progress()
        for i in range(1, n):
            self._patrol(self.all_dead, fleet, prog)
        self.assertEqual(self.dms, [], "no DM before threshold")
        self.assertEqual(called, [], "USB path must not be probed before the debounce clears")

    def test_truly_unreachable_both_paths_DMs_once(self):
        TW.usb_reach_via_rigby = lambda: set()               # rigby answered: 0 on the wire
        detail = self._drive_to_confirm()
        self.assertEqual(len(self.dms), 1, "one DM when BOTH WiFi and USB are dead")
        self.assertIn("truly unreachable", self.dms[0])
        self.assertIn("truly unreachable", detail)

    def test_usb_up_suppresses_DM(self):
        TW.usb_reach_via_rigby = lambda: {11, 12, 13, 14, 15, 16, 17}
        detail = self._drive_to_confirm()
        self.assertEqual(self.dms, [], "tablets alive over USB -> WiFi-only outage must not DM")
        self.assertIn("over USB", detail)

    def test_rigby_unreachable_is_inconclusive_DM(self):
        TW.usb_reach_via_rigby = lambda: None                # could not reach rigby
        detail = self._drive_to_confirm()
        self.assertEqual(len(self.dms), 1)
        self.assertIn("UNCONFIRMED", self.dms[0])
        self.assertIn("INCONCLUSIVE", detail)

    def test_blip_that_recovers_resets_and_never_DMs(self):
        n = TW.UNREACH_CONFIRM
        fleet, prog = TW.FleetReach(), TW.Progress()
        # (n-1) zeros, a recovery, then (n-1) zeros: no run ever reaches n
        for health in [self.all_dead] * (n - 1) + [self.all_up] + [self.all_dead] * (n - 1):
            self._patrol(health, fleet, prog)
        self.assertEqual(self.dms, [], "a recovery mid-streak resets the counter -- no DM")

    def test_healthy_poll_reports_and_never_DMs(self):
        _, detail = self._patrol(self.all_up, TW.FleetReach(), TW.Progress())
        self.assertEqual(self.dms, [])
        self.assertIn("7/7", detail)

    def test_fleetreach_streak_and_reset(self):
        fr = TW.FleetReach()
        self.assertEqual(fr.observe(0), 1)
        self.assertEqual(fr.observe(0), 2)
        self.assertEqual(fr.observe(3), 0, "any node answering resets the streak")
        self.assertEqual(fr.observe(0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
