"""Activity checks Tautulli first, falling back to the Plex API if Tautulli fails.
Session fixtures match live Plex shapes: direct-play uses Metadata only, transcode includes videoDecision/audioDecision."""
import unittest
from unittest import mock

import config
import plex


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


def _pms(*sessions):
    return _Resp({"MediaContainer": {"size": len(sessions), "Metadata": list(sessions)}})


DIRECT = {"type": "episode", "title": "Ep", "grandparentTitle": "Show", "User": {"title": "viewer"},
          "Media": [{"videoCodec": "h264", "Part": [{"decision": "directplay"}]}]}
VIDEO_TX = dict(DIRECT, TranscodeSession={"videoDecision": "transcode", "audioDecision": "copy"})
AUDIO_TX = dict(DIRECT, TranscodeSession={"videoDecision": "copy", "audioDecision": "transcode"})
COPY = dict(DIRECT, TranscodeSession={"videoDecision": "copy", "audioDecision": "copy"})


class PlexActivityFallback(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "PLEX_TOKEN", "x" * 20)
        p.start()
        self.addCleanup(p.stop)

    def _get(self, tautulli, pms):
        def get(url, **kw):
            if "/api/v2" in url:
                if isinstance(tautulli, Exception):
                    raise tautulli
                return tautulli
            return pms
        return mock.patch.object(plex.httpx, "get", side_effect=get)

    def test_tautulli_answers_first(self):
        t = _Resp({"response": {"data": {"stream_count": 1, "stream_count_transcode": 1, "sessions": []}}})
        with self._get(t, _pms()) as g:
            a = plex.activity()
        self.assertEqual((a["source"], a["transcodes"]), ("tautulli", 1))
        self.assertEqual(g.call_count, 1, "Plex must not be asked when Tautulli answered")

    def test_tautulli_down_asks_plex(self):
        with self._get(ConnectionError("tautulli down"), _pms(DIRECT, VIDEO_TX, AUDIO_TX, COPY)):
            a = plex.activity()
        self.assertEqual(a["source"], "plex")
        self.assertEqual((a["streams"], a["transcodes"], a["direct"]), (4, 2, 2),
                         "video or audio transcode counts; copy and direct play do not")

    def test_direct_play_only_is_zero_transcodes(self):
        with self._get(ConnectionError("down"), _pms(DIRECT)):
            self.assertEqual(plex.activity()["transcodes"], 0)

    def test_neither_answers_is_none(self):
        with self._get(ConnectionError("down"), _Resp({}, status=401)):
            self.assertIsNone(plex.activity())

    def test_no_token_means_plex_is_not_asked(self):
        with mock.patch.object(config, "PLEX_TOKEN", ""), self._get(ConnectionError("down"), _pms(VIDEO_TX)) as g:
            self.assertIsNone(plex.activity())
        self.assertEqual(g.call_count, 1)


if __name__ == "__main__":
    unittest.main()
