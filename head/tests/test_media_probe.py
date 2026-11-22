import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import media_tools


def _fake_run(stdout, rc=0, stderr=""):
    return type("R", (), {"returncode": rc, "stdout": stdout, "stderr": stderr})()


class MediaProbePathScopingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = config.MEDIA_DIR
        config.MEDIA_DIR = self.dir
        self.f = os.path.join(self.dir, "ep.mkv")
        Path(self.f).write_text("x")

    def tearDown(self):
        config.MEDIA_DIR = self._orig

    def test_rejects_path_outside_media(self):
        self.assertIn("inside", media_tools.media_probe("/etc/passwd"))

    def test_missing_file_under_media(self):
        self.assertIn("no such file", media_tools.media_probe("nope.mkv"))

    def test_relative_and_absolute_resolve(self):
        with patch("media_tools.subprocess.run", return_value=_fake_run('{"streams":[]}')):
            self.assertIn("Media probe: ep.mkv", media_tools.media_probe("ep.mkv"))
            self.assertIn("Media probe: ep.mkv", media_tools.media_probe(self.f))


class MediaProbeParseTests(unittest.TestCase):
    FF = json.dumps({"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "jpn", "title": "Japanese"}},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng", "title": "English"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
    ]})

    def test_dual_audio_detected(self):
        with patch("media_tools._resolve_media_path", lambda p: "/media/ep.mkv"), \
             patch("media_tools.subprocess.run", return_value=_fake_run(self.FF)):
            out = media_tools.media_probe("/media/ep.mkv")
        self.assertIn("Dual-audio: YES", out)
        self.assertIn("jpn", out)
        self.assertIn("eng", out)
        self.assertIn("Subtitles:", out)
        self.assertIn("h264 1920x1080", out)

    def test_single_audio_not_dual(self):
        ff = json.dumps({"streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng"}}]})
        with patch("media_tools._resolve_media_path", lambda p: "/media/ep.mkv"), \
             patch("media_tools.subprocess.run", return_value=_fake_run(ff)):
            out = media_tools.media_probe("/media/ep.mkv")
        self.assertIn("Dual-audio: no", out)

    def test_ffprobe_missing_is_graceful(self):
        with patch("media_tools._resolve_media_path", lambda p: "/media/ep.mkv"), \
             patch("media_tools.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIn("ffprobe is not installed", media_tools.media_probe("/media/ep.mkv"))


class MediaProbeWiringTests(unittest.TestCase):
    TOOLS = Path("tools.py").read_text(encoding="utf-8")

    def test_wired_and_read_only(self):
        self.assertIn('"name": "media_probe"', self.TOOLS)
        self.assertIn('if name == "media_probe":', self.TOOLS)
        admin_block = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        self.assertNotIn("media_probe", admin_block)   # read-only -> not admin-gated
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.TOOLS.splitlines() if l.startswith(prefix))
            self.assertIn("_MEDIA_PROBE", line)


if __name__ == "__main__":
    unittest.main()
