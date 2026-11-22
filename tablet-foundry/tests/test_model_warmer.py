"""Keep the 35B in the page cache: read back only uncached parts, strictly when both lanes are idle and MemAvailable is sufficient, and never acquire locks."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rigby_card_keeper  # noqa: E402
import rigby_model_warmer as W  # noqa: E402

GB = 10 ** 9


class Decide(unittest.TestCase):
    def test_fully_cached_is_nothing(self):
        self.assertEqual(W.decide(0, [], 20 * GB)[0], "none")

    def test_missing_with_idle_lanes_and_room_reads_back(self):
        action, why = W.decide(5 * GB, [], 20 * GB)
        self.assertEqual(action, "warm", why)

    def test_a_busy_lane_waits_an_order_reads_its_own_model(self):
        action, why = W.decide(5 * GB, ["CURRENT_ORDER.rigby.json"], 20 * GB)
        self.assertEqual(action, "wait")
        self.assertIn("lane is busy", why)

    def test_no_room_waits_rather_than_pushing_out_what_is_in_use(self):
        action, why = W.decide(17 * GB, [], 6 * GB)
        self.assertEqual(action, "wait")
        self.assertIn("only 6.0 GB available", why)

    def test_could_not_tell_the_memory_is_never_room(self):
        self.assertEqual(W.decide(5 * GB, [], None)[0], "wait")


class Warm(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.path = os.path.join(self.d, "m.gguf")
        with open(self.path, "wb") as f:
            f.write(b"x" * 1000)

    def test_only_the_chunks_that_are_not_cached_are_read(self):
        chunks = [(0, 400, True), (400, 400, False), (800, 200, False)]
        seen = []
        n, _secs = W.warm(self.path, chunks, read=lambda fd, off, length: seen.append((off, length)) or length)
        self.assertEqual(seen, [(400, 400), (800, 200)])
        self.assertEqual(n, 600)

    def test_the_default_reader_reads_the_bytes(self):
        n, _secs = W.warm(self.path, [(0, 1000, False)])
        self.assertEqual(n, 1000)


@unittest.skipUnless(sys.platform.startswith("linux"), "mincore is Linux (the gate container and rigby)")
class CachedChunksOnARealFile(unittest.TestCase):
    """Positive control for the instrument: a file just written and read is in the page cache, every chunk says so, and
    the chunks cover the whole file - an instrument that reports nothing cached for a hot file would warm forever."""

    def test_a_hot_file_reads_as_cached_and_the_chunks_cover_it(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "hot.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(3 * 4096 + 17))
        with open(p, "rb") as f:
            f.read()
        chunks = W.cached_chunks(p, chunk=4096)
        self.assertEqual(sum(c[1] for c in chunks), 3 * 4096 + 17)
        self.assertEqual([c[0] for c in chunks], [0, 4096, 8192, 12288])
        self.assertTrue(all(c[2] for c in chunks), chunks)


class MainAndUnits(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.models = os.path.join(self.d, "models")
        os.makedirs(self.models)
        for x in (mock.patch.object(W, "WARM", ("warm-test.gguf",)),     # the MODEL_WARMER_FILES setting, as a fixture
                  mock.patch.object(W, "STATE", self.d), mock.patch.object(W, "MODELS", self.models),
                  mock.patch.object(W, "mem_available", return_value=10 * GB)):
            x.start()
            self.addCleanup(x.stop)
        with open(os.path.join(self.models, W.WARM[0]), "wb") as f:
            f.write(b"y" * 100)

    def test_a_run_warms_and_stamps_its_heartbeat(self):
        calls = iter([[(0, 100, False)], [(0, 100, True)]])
        with mock.patch.object(W, "cached_chunks", side_effect=lambda p: next(calls)):
            W.main([])
        rec = json.load(open(os.path.join(self.d, W.RECORD), encoding="utf-8"))
        self.assertEqual(rec["period_s"], W.PERIOD_S)
        f = rec["files"][W.WARM[0]]
        self.assertEqual((f["action"], f["cached_pct"], f["cached_pct_after"]), ("warm", 0.0, 100.0))

    def test_dry_reads_nothing_and_writes_nothing(self):
        with mock.patch.object(W, "cached_chunks", return_value=[(0, 100, False)]), \
                mock.patch.object(W, "warm") as wm:
            W.main(["--dry"])
        wm.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.d, W.RECORD)))

    def test_a_busy_lane_is_read_from_tolkiens_own_claims(self):
        open(os.path.join(self.d, "CURRENT_ORDER.rigby.json"), "w").close()
        with mock.patch.object(W, "cached_chunks", return_value=[(0, 100, False)]), \
                mock.patch.object(W, "warm") as wm:
            W.main([])
        wm.assert_not_called()
        rec = json.load(open(os.path.join(self.d, W.RECORD), encoding="utf-8"))
        self.assertEqual(rec["files"][W.WARM[0]]["action"], "wait")

    def test_unset_settings_mean_no_action_and_say_so(self):
        # a host with no /etc/default/tolkien-host: the warmer touches nothing and names the settings
        with mock.patch.object(W, "WARM", ()), mock.patch.object(W, "cached_chunks") as cc, \
             mock.patch("builtins.print") as out:
            self.assertEqual(W.main([]), 2)
        cc.assert_not_called()
        self.assertIn("MODEL_WARMER_FILES", " ".join(str(c) for c in out.call_args_list))

    def test_the_service_unit_loads_the_host_settings_file(self):
        self.assertIn("EnvironmentFile=-%s\n" % W.ENVFILE, W.UNITS["rigby-model-warmer.service"])
        self.assertEqual(W.ENVFILE, rigby_card_keeper.ENVFILE, "one file for both host timers")

    def test_the_units_run_at_its_own_cadence_and_at_idle_priority(self):
        timer, service = W.UNITS["rigby-model-warmer.timer"], W.UNITS["rigby-model-warmer.service"]
        self.assertIn("OnUnitActiveSec=%ds" % W.PERIOD_S, timer)
        self.assertIn("IOSchedulingClass=idle", service)
        self.assertIn("Nice=19", service)

    def test_one_derivation_with_the_card_keeper(self):
        """Verify lane claims and beat match the card keeper to prevent drift."""
        self.assertEqual(W.LANE_CLAIMS, rigby_card_keeper.LANE_CLAIMS)
        self.assertEqual(W.PERIOD_S, rigby_card_keeper.PERIOD_S)


if __name__ == "__main__":
    unittest.main()
