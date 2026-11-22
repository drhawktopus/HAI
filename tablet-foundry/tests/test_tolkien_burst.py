"""Stage 2 burst analysis — hardware-decode a recorded clip, detect over its frames.

The properties pinned here are the ones that would otherwise pass a review and fail in service:
a decoder that starts and emits nothing, a detector failure laundered into "empty room", and a
75 MB byproduct left on a tablet with no room for it.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as tf  # noqa: E402

# Capture the genuine Popen at import time to build instance specs for mocking.
# Lazy resolution fails because test guards may have already patched Popen,
# causing create_autospec to raise InvalidSpecError on a mock object.
_REAL_POPEN = subprocess.Popen


class BurstIsARegisteredCapability(unittest.TestCase):
    def test_burst_is_advertised(self):
        """The set of registered names IS the agent's capability list -- an unwired capability fails
        exactly like one that does not exist."""
        self.assertIn("burst", tf._FELLOW_HANDLERS)


class PlaybackCommandTargetsRECORDINGS(unittest.TestCase):
    """Ensure burst commands target the playback endpoint (`cam/playback`) to avoid silent analysis of live stream data."""

    def cmd(self):
        return tf._burst_playback_cmd(1, "2026-08-09 11:34:00", "2026-08-09 11:35:00",
                                      "admin", "pw", "10.0.0.1")

    def test_it_uses_the_playback_endpoint(self):
        c = self.cmd()
        self.assertIn("cam/playback", c)
        self.assertNotIn("realmonitor", c)

    def test_the_time_format_matches_THE_LABS_PROVEN_PULLER(self):
        """Time format must use underscores, not dashes. Dashes cause silent hangs on the device."""
        c = self.cmd()
        self.assertIn("starttime=2026_08_09_11_34_00", c)
        self.assertIn("endtime=2026_08_09_11_35_00", c)
        self.assertNotIn("2026-08-09_11-34-00", c, "dashes are the format that silently hangs")

    def test_the_PASSWORD_is_percent_encoded(self):
        """Passwords with special chars (!, $) must be percent-encoded in RTSP URLs,
        or authentication fails."""
        c = tf._burst_playback_cmd(1, "2026-08-09 11:34:00", "2026-08-09 11:35:00",
                                   "admin", "Ex4mple-Pw!$", "10.0.0.1")
        self.assertIn("admin:Ex4mple-Pw%21%24@", c)
        self.assertNotIn("Ex4mple-Pw!$@", c)

    def test_the_time_helper_is_reusable_and_exact(self):
        self.assertEqual(tf._burst_rtsp_time("2026-08-09 11:34:03"), "2026_08_09_11_34_03")

    def test_channel_is_carried(self):
        self.assertIn("channel=1", self.cmd())

    def test_it_copies_rather_than_re_encodes(self):
        """`-c:v copy` -- the tablet's hardware decoder is the point. Software re-encoding on the way
        in would burn the CPU the whole design exists to avoid."""
        self.assertIn("-c:v copy", self.cmd())


class FakePopen(object):
    """Configuration carrier for the fake `ffmpeg | hwdec` pipe. NOT the fake itself.

    This class only holds per-test knobs (attributes); the actual mock is built and installed
    by `install_fake_popen` using `mock.patch.object(..., autospec=True)` to prevent signature
    drift between the fake and the real `subprocess.Popen`."""

    on_hwdec = None
    raise_timeout = False
    hwdec_stderr = b""
    timeout_stderr = b""

    @classmethod
    def reset(cls):
        cls.on_hwdec = None
        cls.raise_timeout = False
        cls.hwdec_stderr = b""
        cls.timeout_stderr = b""


def install_fake_popen(testcase):
    """Patch `subprocess.Popen` with an AUTOSPEC'd fake for the duration of one test.

    Both the class call and every instance method are spec'd against the real `subprocess.Popen`, so
    a call this code could not really make fails loudly at the seam instead of silently passing."""
    # Capture the real class before patching; autospec'ing a mock raises InvalidSpecError.
    real_popen = _REAL_POPEN
    patcher = mock.patch.object(subprocess, "Popen", autospec=True)
    popen = patcher.start()
    testcase.addCleanup(patcher.stop)          # restores even if the test raises

    def _factory(argv, *a, **kw):
        inst = mock.create_autospec(real_popen, instance=True)
        is_hwdec = any("hwdec" in str(x) for x in argv)
        inst.returncode = 0
        inst.stdout = io.BytesIO(b"")
        # autospec misses `args` (set in __init__); `subprocess.run` requires it.
        inst.args = argv
        if is_hwdec and FakePopen.on_hwdec:
            FakePopen.on_hwdec(argv)

        def _communicate(input=None, timeout=None):
            if FakePopen.raise_timeout and is_hwdec:
                raise subprocess.TimeoutExpired(argv, timeout or 0,
                                                stderr=FakePopen.timeout_stderr)
            return b"", (FakePopen.hwdec_stderr if is_hwdec else b"")

        inst.communicate.side_effect = _communicate
        inst.__enter__ = mock.Mock(return_value=inst)
        inst.__exit__ = mock.Mock(return_value=False)
        return inst

    popen.side_effect = _factory
    return popen


class ZeroFramesIsAFAILURE(unittest.TestCase):
    """A decoder may start cleanly and exit 0 without emitting frames.
    Zero frames indicates failure regardless of exit code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="burst-")
        self._dir, self._bin = tf.BURST_DIR, tf.HWDEC_BIN
        tf.BURST_DIR = self.tmp
        tf.HWDEC_BIN = os.path.join(self.tmp, "hwdec")
        open(tf.HWDEC_BIN, "wb").close()          # exists, so we get past the binary check
        FakePopen.reset()
        install_fake_popen(self)
        # Stub probe to return {} to deliberately trigger the geometry fallback path.
        # These tests verify frame accounting, not geometry; we want to exercise the
        # intended fallback logic, not rely on side effects of interface mismatches.
        self._probe = tf._burst_probe_geometry
        tf._burst_probe_geometry = lambda *a, **k: {}

    def tearDown(self):
        tf.BURST_DIR, tf.HWDEC_BIN = self._dir, self._bin
        tf._burst_probe_geometry = self._probe

    def _req(self, **kw):
        r = {"channel": 1, "start": "2026-08-09 11:34:00", "end": "2026-08-09 11:35:00",
             "user": "u", "pw": "p", "nvr": "10.0.0.116"}
        r.update(kw)
        return r

    def test_a_clean_exit_with_no_output_is_reported_as_a_failure(self):
        FakePopen.hwdec_stderr = b"codec started; 0 frames"
        out = tf._fellow_burst(self._req())
        self.assertFalse(out["ok"], "rc=0 with no frames must NOT read as success")
        self.assertEqual(out["decoded"], 0)
        self.assertIn("ZERO frames", out["error"])

    def test_the_failure_carries_the_decoders_own_stderr(self):
        """So the cause is evidence rather than guesswork -- the decoder names which codec it chose
        and why it gave up."""
        FakePopen.hwdec_stderr = b"cap 0 -- queueing empty"
        out = tf._fellow_burst(self._req())
        self.assertIn("cap 0", out["decoder_stderr"])

    def test_CONTROL_a_burst_that_DOES_produce_frames_succeeds_and_counts_them(self):
        """Positive control for the zero-frames test below: without a successful baseline verifying
        that frames are actually counted, a result of 0 could be trivial (e.g., decoder stubbed out),
        making the negative test meaningless."""
        sw, sh = 704, 480
        fsz = sw * sh * 3 // 2
        nframes = 4
        raw_written = {}

        def on_hwdec(argv):
            out = argv[argv.index("--out") + 1]
            with open(out, "wb") as fh:
                fh.write(b"\x00" * (fsz * nframes))
            raw_written["path"] = out

        FakePopen.on_hwdec = on_hwdec

        # The frame->JPEG step and the detector are exercised by their own tests; stub them so this
        # one grades the DECODE ACCOUNTING, which is what it is about.
        real_jpeg, real_det = tf._burst_frame_jpeg, tf._burst_detect
        tf._burst_frame_jpeg = lambda *a, **k: b"jpeg"
        tf._burst_detect = lambda _j: [{"label": "cat", "confidence": 0.61}]
        try:
            out = tf._fellow_burst(self._req(scale="704x480", frames=nframes))
        finally:
            tf._burst_frame_jpeg, tf._burst_detect = real_jpeg, real_det

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decoded"], nframes, "frame accounting must match the bytes written")
        self.assertEqual(out["analysed"], nframes)
        self.assertEqual(out["frames_with_detections"], nframes)
        self.assertEqual(out["detections"][0]["predictions"][0]["label"], "cat")
        self.assertFalse(os.path.exists(raw_written["path"]),
                         "the raw YUV byproduct must be deleted even on the SUCCESS path")

    def test_stride_subsamples_without_losing_the_frame_index(self):
        """A long burst need not analyse every frame; the reported index must still refer to the
        real position in the clip, or a detection cannot be located in time afterwards."""
        sw, sh = 704, 480
        fsz = sw * sh * 3 // 2

        def on_hwdec(argv):
            with open(argv[argv.index("--out") + 1], "wb") as fh:
                fh.write(b"\x00" * (fsz * 6))

        FakePopen.on_hwdec = on_hwdec
        real_jpeg, real_det = tf._burst_frame_jpeg, tf._burst_detect
        tf._burst_frame_jpeg = lambda *a, **k: b"jpeg"
        tf._burst_detect = lambda _j: [{"label": "cat", "confidence": 0.6}]
        try:
            out = tf._fellow_burst(self._req(scale="704x480", frames=6, stride=2))
        finally:
            tf._burst_frame_jpeg, tf._burst_detect = real_jpeg, real_det
        self.assertEqual(out["decoded"], 6)
        self.assertEqual(out["analysed"], 3)
        self.assertEqual([f["frame"] for f in out["detections"]], [0, 2, 4])

    def test_missing_credentials_refuses_rather_than_producing_a_401_burst(self):
        out = tf._fellow_burst({"channel": 1, "start": "a", "end": "b"})
        self.assertFalse(out["ok"])
        self.assertIn("credentials", out["error"])

    def test_missing_window_refuses(self):
        out = tf._fellow_burst({"channel": 1, "user": "u", "pw": "p"})
        self.assertFalse(out["ok"])
        self.assertIn("start", out["error"])

    def test_the_decoder_stderr_is_REDACTED_before_it_is_returned(self):
        """Redact credentials from ffmpeg stderr to prevent leaks into
        durable stores (analyses DB, RAG-indexed blocks).
        Redaction must preserve diagnostic messages for error resolution."""
        FakePopen.hwdec_stderr = (b"[rtsp] method DESCRIBE failed for "
                                  b"rtsp://admin:Sup3rSecret%21%24@10.0.0.1:554/cam/playback")
        out = tf._fellow_burst(self._req())
        self.assertFalse(out["ok"])
        self.assertNotIn("Sup3rSecret", out["decoder_stderr"])
        self.assertIn("<redacted>", out["decoder_stderr"])
        self.assertIn("DESCRIBE failed", out["decoder_stderr"],
                      "redaction must preserve the DIAGNOSIS - it is the reason stderr is returned")

    def test_redaction_leaves_credential_free_text_alone(self):
        """Control: a redactor that mangles ordinary output would destroy the evidence it guards."""
        self.assertEqual(tf._redact("cap 0 -- queueing empty"), "cap 0 -- queueing empty")

    def test_the_LANDED_DECODER_NAME_rides_in_the_result(self):
        """`HEVC_TRY[]` falls back to software decoding silently.

        Without this field, a hardware arm may have used software decoding,
        making throughput comparisons uninterpretable since the distinction
        is lost after execution."""
        sw, sh = 704, 480
        fsz = sw * sh * 3 // 2

        def on_hwdec(argv):
            with open(argv[argv.index("--out") + 1], "wb") as fh:
                fh.write(b"\x00" * (fsz * 2))

        FakePopen.on_hwdec = on_hwdec
        FakePopen.hwdec_stderr = (b"hwdec: decoder started: c2.android.hevc.decoder  "
                                  b"3840x2160 video/hevc (no surface, ByteBuffer output)\n")
        real_jpeg, real_det = tf._burst_frame_jpeg, tf._burst_detect
        tf._burst_frame_jpeg = lambda *a, **k: b"jpeg"
        tf._burst_detect = lambda _j: []
        try:
            out = tf._fellow_burst(self._req(scale="704x480", frames=2))
        finally:
            tf._burst_frame_jpeg, tf._burst_detect = real_jpeg, real_det
        self.assertTrue(out["ok"])
        self.assertEqual(out["decoder"], "c2.android.hevc.decoder")

    def test_the_decoder_name_survives_a_LONG_noisy_stderr(self):
        """Regression: searching only the last 600 chars of stderr misses the one-time
        startup line if decode duration exceeds that window. Always search the full
        buffer; truncate only when storing."""
        sw, sh = 704, 480
        fsz = sw * sh * 3 // 2

        def on_hwdec(argv):
            with open(argv[argv.index("--out") + 1], "wb") as fh:
                fh.write(b"\x00" * (fsz * 2))

        FakePopen.on_hwdec = on_hwdec
        FakePopen.hwdec_stderr = (
            b"hwdec: decoder started: c2.mtk.hevc.decoder  3840x2160 video/hevc\n"
            + b"hwdec: [diag] fed=100 frames=90 sent=90 dropped=0 buffered=1234\n" * 40)
        real_jpeg, real_det = tf._burst_frame_jpeg, tf._burst_detect
        tf._burst_frame_jpeg = lambda *a, **k: b"jpeg"
        tf._burst_detect = lambda _j: []
        try:
            out = tf._fellow_burst(self._req(scale="704x480", frames=2))
        finally:
            tf._burst_frame_jpeg, tf._burst_detect = real_jpeg, real_det
        self.assertGreater(len(FakePopen.hwdec_stderr), 600, "fixture must exceed the stored window")
        self.assertEqual(out["decoder"], "c2.mtk.hevc.decoder",
                         "the name must be found in the FULL stderr, not the truncated tail")

    def test_an_UNSEEN_decoder_line_reports_None_rather_than_guessing(self):
        """Older hwdec builds logged `decoder started` with NO name (seen in gandalf's historical
        logs). Unknown must render as unknown — inventing a name would be worse than the gap."""
        sw, sh = 704, 480
        fsz = sw * sh * 3 // 2

        def on_hwdec(argv):
            with open(argv[argv.index("--out") + 1], "wb") as fh:
                fh.write(b"\x00" * (fsz * 2))

        FakePopen.on_hwdec = on_hwdec
        FakePopen.hwdec_stderr = b"hwdec: decoder started (no surface, ByteBuffer output)\n"
        real_jpeg, real_det = tf._burst_frame_jpeg, tf._burst_detect
        tf._burst_frame_jpeg = lambda *a, **k: b"jpeg"
        tf._burst_detect = lambda _j: []
        try:
            out = tf._fellow_burst(self._req(scale="704x480", frames=2))
        finally:
            tf._burst_frame_jpeg, tf._burst_detect = real_jpeg, real_det
        self.assertIsNone(out["decoder"])

    def test_the_burst_holds_NO_credentials_of_its_own(self):
        """The node must never carry a standing NVR credential -- the whole open backlog item is
        that two of them already do, in `ps`, where anything can read it."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "tablet_foundry.py"), encoding="utf-8") as fh:
            src = fh.read()
        seg = src[src.index("def _fellow_burst"):src.index("def _burst_frame_jpeg")]
        self.assertNotIn("Ex4mp", seg, "a literal credential must never appear in the burst path")


class TheCLIPDecidesItsOwnGeometry(unittest.TestCase):
    """Heterogeneous archive: clips carry their own geometry. Pre-08-12 footage is 4K and requires
    gandalf; post-08-12 is 2688x1520@30 and any fellow can decode."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="burstgeo-")
        self._dir, self._bin = tf.BURST_DIR, tf.HWDEC_BIN
        tf.BURST_DIR = self.tmp
        tf.HWDEC_BIN = os.path.join(self.tmp, "hwdec")
        open(tf.HWDEC_BIN, "wb").close()
        self._probe = tf._burst_probe_geometry
        self.argv = {}
        FakePopen.reset()
        FakePopen.on_hwdec = lambda argv: self.argv.__setitem__("seen", argv)
        FakePopen.hwdec_stderr = b"no frames"
        install_fake_popen(self)

    def tearDown(self):
        tf.BURST_DIR, tf.HWDEC_BIN = self._dir, self._bin
        tf._burst_probe_geometry = self._probe

    def _req(self, **kw):
        r = {"channel": 1, "start": "2026-08-09 11:34:00", "end": "2026-08-09 11:35:00",
             "user": "u", "pw": "p", "nvr": "10.0.0.116"}
        r.update(kw)
        return r

    def test_the_PROBED_geometry_beats_the_callers_hint(self):
        tf._burst_probe_geometry = lambda *a, **k: {"width": 3840, "height": 2160}
        tf._fellow_burst(self._req(width=2688, height=1520))
        argv = self.argv["seen"]
        self.assertEqual(argv[argv.index("--width") + 1], "3840")
        self.assertEqual(argv[argv.index("--height") + 1], "2160")

    def test_an_UNANSWERABLE_probe_falls_back_to_the_hint_rather_than_guessing(self):
        """A probe that cannot answer returns {} — never a confident number."""
        tf._burst_probe_geometry = lambda *a, **k: {}
        tf._fellow_burst(self._req(width=2688, height=1520))
        argv = self.argv["seen"]
        self.assertEqual(argv[argv.index("--width") + 1], "2688")

    def test_the_probe_parses_real_ffprobe_output(self):
        """Pinned against the ACTUAL bytes ffprobe returned for the 08-09 clip, not a paraphrase."""
        real = subprocess.run

        class R(object):
            returncode = 0
            stdout = b"codec_name=hevc\nwidth=3840\nheight=2160\n"
            stderr = b""

        subprocess.run = lambda *a, **k: R()
        try:
            out = tf._burst_probe_geometry(1, "2026-08-09 11:34:00", "2026-08-09 11:35:00",
                                           "u", "p", "10.0.0.1")
        finally:
            subprocess.run = real
        self.assertEqual((out["width"], out["height"], out["codec"]), (3840, 2160, "hevc"))

    def test_a_probe_that_THROWS_returns_empty_not_an_exception(self):
        """The probe runs before the decode; if it could raise it would take the whole burst with
        it, and a geometry hint is a convenience rather than a reason to fail."""
        real = subprocess.run

        def boom(*a, **k):
            raise OSError("ffprobe missing")

        subprocess.run = boom
        try:
            self.assertEqual(tf._burst_probe_geometry(1, "a", "b", "u", "p", "h"), {})
        finally:
            subprocess.run = real


class DetectorFailuresAreNOTLaunderedIntoEmptyRooms(unittest.TestCase):
    """Detector errors (503) must propagate, not be laundered into empty responses.
    Silencing them here would hide failures for upstream consumers."""

    def test_a_failed_detector_response_raises(self):
        import urllib.request

        class Resp(object):
            def read(self):
                return json.dumps({"success": False, "error": "model not loaded"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: Resp()
        try:
            with self.assertRaises(RuntimeError):
                tf._burst_detect(b"notreallyajpeg")
        finally:
            urllib.request.urlopen = real

    def test_an_empty_prediction_list_is_returned_as_empty_not_as_an_error(self):
        """The other direction matters just as much: a genuinely empty frame is a real answer."""
        import urllib.request

        class Resp(object):
            def read(self):
                return json.dumps({"success": True, "predictions": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: Resp()
        try:
            self.assertEqual(tf._burst_detect(b"x"), [])
        finally:
            urllib.request.urlopen = real

    def test_it_posts_to_the_RESIDENT_detector_on_this_node(self):
        """Not a per-frame `ncnn_detect` spawn: that reloads an 11 MB model every frame and the load
        dwarfs the ~27 ms inference. PROJECT #32 exists for this."""
        import urllib.request
        seen = {}

        class Resp(object):
            def read(self):
                return json.dumps({"success": True, "predictions": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def spy(req, timeout=None):
            seen["url"] = req.full_url
            return Resp()

        real = urllib.request.urlopen
        urllib.request.urlopen = spy
        try:
            tf._burst_detect(b"x")
        finally:
            urllib.request.urlopen = real
        self.assertIn("127.0.0.1:%d" % tf.DETECT_PORT, seen["url"])
        self.assertIn("/v1/vision/detection", seen["url"])


class TheRawYUVIsAByproduct(unittest.TestCase):
    """~0.5 MB per 704x480 frame; 150 frames is ~75 MB. Leaving those on a tablet is the
    staged-adapter mistake in miniature, on hardware with far less room to absorb it."""

    def test_cleanup_removes_the_raw_file(self):
        fd, path = tempfile.mkstemp(suffix=".yuv")
        os.close(fd)
        self.assertTrue(os.path.exists(path))
        tf._burst_cleanup(path)
        self.assertFalse(os.path.exists(path))

    def test_cleanup_never_raises_on_a_missing_file(self):
        """Cleanup runs on every exit path including the failures. A cleanup that can throw replaces
        the exception in flight with its own and destroys the diagnosis it exists to protect."""
        tf._burst_cleanup("/nonexistent/nope.yuv")


class TheEngineClassSaysWhichCHIPDidTheWork(unittest.TestCase):
    """Test that vendor-specific decoders (MTK/QCOM) map to 'dedicated' hardware,
    distinguishing them from AOSP/CPU fallbacks which would otherwise obscure
    whether the dedicated decode chipset or the CPU is performing the work."""

    def test_vendor_blocks_are_dedicated(self):
        for name in ("OMX.MTK.VIDEO.DECODER.HEVC", "c2.mtk.hevc.decoder",
                     "OMX.qcom.video.decoder.hevc", "c2.qti.hevc.decoder"):
            self.assertEqual(tf._engine_class(name), "dedicated", name)

    def test_aosp_decoders_are_CPU_not_merely_software(self):
        """'software' obscures the point: it is the CPU doing video work while a decode chip idles."""
        for name in ("c2.android.hevc.decoder", "OMX.google.hevc.decoder"):
            self.assertEqual(tf._engine_class(name), "cpu", name)

    def test_unknown_is_None_and_never_a_GUESS(self):
        """An unlabelled row is honest. A mislabelled one is what this function exists to prevent."""
        for name in (None, "", "some.new.decoder.nobody.has.seen"):
            self.assertIsNone(tf._engine_class(name))

    def test_the_classes_are_disjoint_and_no_gpu_class_exists(self):
        """hwdec passes NO SURFACE to AMediaCodec_configure, so no decode path here touches the GPU.
        A 'gpu' class would be a category error dressed as thoroughness."""
        seen = {tf._engine_class(n) for n in
                ("c2.mtk.hevc.decoder", "c2.android.hevc.decoder", "OMX.qcom.video.decoder.hevc")}
        self.assertEqual(seen, {"dedicated", "cpu"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
