"""The factory tile's `tok_s` field: parsed from the `· N tok/s` every factory appends to its published phase."""
import datetime
import sys
import unittest
from unittest import mock

import app


def _iso(seconds_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=seconds_ago)).isoformat()


class _FakeQueue:
    @staticmethod
    def gpu_status_hosts():
        return {}

    @staticmethod
    def gpu_status(host=None):
        return {}


def _tile(host, phase, seconds_ago=20, state="compute"):
    rec = {"host": host, "name": "hardbench-x", "state": state, "step": 24, "total_steps": 75,
           "phase": phase, "ts": _iso(seconds_ago)}
    with mock.patch.object(app, "_fleet_run_all", return_value={host: rec}), \
         mock.patch.object(app, "_rescue_all", return_value={}), \
         mock.patch.dict(sys.modules, {"p20_queue": _FakeQueue}):
        return app._factory_view(host)


class FactoryTokS(unittest.TestCase):
    def test_gpu_factory_phase_rate_lands_on_the_tile(self):
        t = _tile("mothership", "compute(gpu): infer_ollama.py · 1.7 s/unit · 93.4 tok/s")
        self.assertEqual(t["tok_s"], "93.4 tok/s")
        self.assertEqual(t["rate"], "1.7 s/unit", "the per-unit rate still parses beside it")

    def test_fellowship_phase_rate_lands_on_the_tile(self):
        self.assertEqual(_tile("fellowship", "llm-rigby · 7.5 tok/s", state="running")["tok_s"], "7.5 tok/s")

    def test_no_published_rate_and_a_dead_run_show_nothing(self):
        self.assertEqual(_tile("mothership", "compute(gpu): infer_ollama.py · 1.7 s/unit")["tok_s"], "")
        self.assertEqual(_tile("mothership", "compute(gpu): x · 93.4 tok/s", seconds_ago=7200)["tok_s"], "",
                         "a stale record is not a live speed")


if __name__ == "__main__":
    unittest.main()
