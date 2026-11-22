"""Guard against models emitting image markdown tags for files they did not generate.
Verification checks the filesystem before persisting and returning results."""
import pathlib
import tempfile
import unittest

import app
import comfy_client


class ImgVerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig = comfy_client._images_dir
        comfy_client._images_dir = lambda: self.tmp

    def tearDown(self):
        comfy_client._images_dir = self._orig

    def test_real_image_kept_fake_stripped(self):
        d = self.tmp / "Alex"
        d.mkdir(parents=True)
        (d / "real.png").write_bytes(b"x")
        text = ("Here! ![real](/images/Alex/real.png) and also "
                "![fake](/images/Alex/Mona-Lisa-but-it's-Patrick-Star.png)")
        out = app._verify_image_refs(text, "Alex")
        self.assertIn("![real](/images/Alex/real.png)", out)
        self.assertNotIn("Patrick-Star.png)", out)
        self.assertIn("never actually generated", out)

    def test_no_image_refs_untouched(self):
        self.assertEqual(app._verify_image_refs("plain answer", "Alex"), "plain answer")
        self.assertIsNone(app._verify_image_refs(None, "Alex"))

    def test_wired_into_persist_and_response(self):
        src = open("app.py", encoding="utf-8").read()
        self.assertIn("answer = _verify_image_refs(answer, q.user)",
                      src.split("def _persist(", 1)[1].split("\ndef ", 1)[0])
        self.assertIn('result[_k] = _verify_image_refs(result[_k], q.user)',
                      src.split("def _run_query(", 1)[1].split("\ndef ", 1)[0])


class RenderBusyRoutingTests(unittest.TestCase):

    def test_render_busy_fail_soft(self):
        from unittest import mock
        # render_busy() checks two sources: ComfyUI /queue and GPU status.
        # Mock both to isolate the httpx fail-soft logic; otherwise, real
        # GPU activity (e.g., stale 'training' from a long run) may cause
        # false negatives if not mocked.
        import p20_queue
        FREE, TRAINING = ({"state": "free"}, 10), ({"state": "training"}, 10)
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=FREE):
            with mock.patch("httpx.get", side_effect=OSError("down")):
                self.assertFalse(comfy_client.render_busy())
            resp_busy = mock.Mock()
            resp_busy.json.return_value = {"queue_running": [["job"]]}
            with mock.patch("httpx.get", return_value=resp_busy):
                self.assertTrue(comfy_client.render_busy())
        # And the heartbeat path on its own: a training card is busy even with ComfyUI silent.
        with mock.patch("httpx.get", side_effect=OSError("down")):
            with mock.patch.object(p20_queue, "gpu_status_any", return_value=TRAINING):
                self.assertTrue(comfy_client.render_busy())
            with mock.patch.object(p20_queue, "gpu_status_any", return_value=FREE):
                self.assertFalse(comfy_client.render_busy())

    def test_pick_brain_defers_busy_mothership(self):
        src = open("app.py", encoding="utf-8").read()
        body = src.split("def pick_brain(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("comfy_client.render_busy()", body)
        self.assertIn("render-busy -> waiting for %s's wake", body)
        # step 4's woken/ensure path remains UNCONDITIONAL — availability never regresses
        self.assertIn("_ensure_lead()", body)

    def test_pick_brain_waits_out_bighonkers_wake_first(self):
        import config
        src = open("app.py", encoding="utf-8").read()
        body = src.split("def pick_brain(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("config.BRAIN_WAKE_WAIT", body)
        self.assertIn('config.BRAIN_LABEL + " (woken)"', body)
        wait_pos = body.index("BRAIN_WAKE_WAIT")
        mothership_pos = body.index("LEAD_BOX_BRAIN_MODEL")
        self.assertLess(wait_pos, mothership_pos)      # the wait comes BEFORE the fallback
        self.assertLessEqual(config.BRAIN_WAKE_WAIT, 60)   # bounded — a dead box can't hang us


if __name__ == "__main__":
    unittest.main()
