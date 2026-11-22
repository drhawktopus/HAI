"""menu_wall: the whole-box wall price per menu answer. The seam tests run the REAL remote reader (wall_tail.py) locally and
parse its stream with the real consumer - producer and consumer must agree, or the sync silently pulls nothing."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_fill  # noqa: E402
import menu_wall as W  # noqa: E402


def tail(*args):
    with open(W.TAIL, "rb") as f:
        return subprocess.run([sys.executable, "-", *args], stdin=f, capture_output=True, check=True).stdout


class Isolated(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._saved = (W.CACHE, W.CALIBRATION, W.SS_TELEM, W._CAL, dict(W._LOADED), W._RIGBY_BUSY, W.OUTBOX)
        W.CACHE = os.path.join(self.root, "cache")
        W.CALIBRATION = os.path.join(self.root, "cal.json")
        W.SS_TELEM = os.path.join(self.root, "ss")
        W.OUTBOX = os.path.join(self.root, "outbox")
        for d in (W.SS_TELEM, W.OUTBOX):
            os.makedirs(d)
        W._CAL, W._RIGBY_BUSY = None, None
        W._LOADED.clear()

    def tearDown(self):
        W.CACHE, W.CALIBRATION, W.SS_TELEM, W._CAL, loaded, W._RIGBY_BUSY, W.OUTBOX = self._saved
        W._LOADED.clear()
        W._LOADED.update(loaded)
        shutil.rmtree(self.root, ignore_errors=True)

    def cal(self, **boxes):
        with open(W.CALIBRATION, "w", encoding="utf-8") as f:
            json.dump({"boxes": boxes}, f)
        W._CAL = None


class TheSyncSeam(Isolated):
    def test_the_reader_and_the_consumer_agree_byte_for_byte_and_only_new_bytes_move(self):
        src = os.path.join(self.root, "remote.csv")
        first = b"ts,epoch,active_w\r\nrow1\r\n"
        with open(src, "wb") as f:
            f.write(first)
        local = os.path.join(self.root, "local.csv")
        frames = W._frames(tail("%s|0" % src))
        self.assertEqual(len(frames), 1, "one frame per argument")
        path, off, data, size = frames[0]
        self.assertEqual((path, off, size), (src, "0", len(first)))
        self.assertEqual(W._append(local, off, data, size), len(first))
        with open(src, "ab") as f:
            f.write(b"row2\r\n")
        (_, off, data, size), = W._frames(tail("%s|%d" % (src, os.path.getsize(local))))
        self.assertEqual((off, data), (str(len(first)), b"row2\r\n"), "the reader sends only what the cache lacks")
        W._append(local, off, data, size)
        with open(src, "rb") as a, open(local, "rb") as b:
            self.assertEqual(a.read(), b.read(), "CRLF and all: bytes, not text")

    def test_a_shrunk_remote_file_restarts_the_copy_and_a_missing_one_moves_nothing(self):
        src = os.path.join(self.root, "remote.csv")
        local = os.path.join(self.root, "local.csv")
        with open(local, "wb") as f:
            f.write(b"x" * 100)
        with open(src, "wb") as f:
            f.write(b"new\n")
        (_, off, data, size), = W._frames(tail("%s|100" % src))       # asked past its end: the reader starts at 0
        self.assertEqual(off, "0")
        W._append(local, off, data, size)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"new\n")
        (_, _, data, size), = W._frames(tail("%s|0" % os.path.join(self.root, "nope.csv")))
        self.assertEqual((data, size, W._append(local, "0", data, size)), (b"", -1, 0))

    def test_the_listing_names_every_file_with_its_size(self):
        for n, body in (("gpu_telemetry-20261001.csv", b"abc"), ("gpu_telemetry-20261002.csv", b"abcdef")):
            with open(os.path.join(self.root, n), "wb") as f:
                f.write(body)
        (_, off, listing, _), = W._frames(tail(os.path.join(self.root, "gpu_telemetry-2026*.csv") + "|LIST"))
        self.assertEqual(off, "LIST")
        self.assertEqual(dict(l.split("\t") for l in listing.decode().splitlines()),
                         {"gpu_telemetry-20261001.csv": "3", "gpu_telemetry-20261002.csv": "6"})

    def test_a_torn_stream_is_refused_not_half_read(self):
        with self.assertRaises(AssertionError):
            W._frames(b"p\t0\t10\t10\nabc")


class SeriesWindow(unittest.TestCase):
    def test_time_weighted_mean_and_a_silence_is_a_gap_not_a_reading(self):
        s = W.Series([(0.0, 100.0), (10.0, 200.0), (100.0, 300.0)])
        self.assertEqual(s.window(0, 20), (150.0, 20.0))
        mean, cov = s.window(0, 100)                 # 200 W speaks for GAP_S only, then the log is silent until 100
        self.assertEqual(cov, 10.0 + W.GAP_S)
        self.assertAlmostEqual(mean, (100 * 10 + 200 * W.GAP_S) / cov)
        self.assertEqual(W.Series([]).window(0, 10), (None, 0.0))


class ParsedCache(Isolated):
    def test_the_parse_advances_over_new_lines_only_and_waits_for_a_line_still_being_written(self):
        p = os.path.join(self.root, "ups_power.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("ts,epoch,active_w\n2026-10-30T00:00:00,100.0,500\n2026-10-30T00:00:05,105.0,51")
        st = W._parsed("mothership", p, W._x_ups)
        self.assertEqual(st["wall"], [(100.0, 500.0)], "the unterminated row waits")
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write("0\n")
        st = W._parsed("mothership", p, W._x_ups)
        self.assertEqual(st["wall"], [(100.0, 500.0), (105.0, 510.0)])
        self.assertEqual(st["offset"], os.path.getsize(p))

    def test_a_header_mid_file_rekeys_the_columns(self):
        p = os.path.join(self.root, "t.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("ts,power_w,ups_load_pct,ups_status\n2026-10-30 00:00:00,10,20,ONLINE\n"
                    "ts,ups_status,ups_load_pct,power_w\n2026-10-30 00:00:01,ONLINE,30,11\n"
                    "ts,power_w,ups_load_pct,ups_status\n2026-10-30 00:00:02,12,,COMMLOST\n")
        st = W._parsed("bighonker", p, W._x_ups_telemetry(4.5))
        self.assertEqual([v for _, v in st["card"]], [10.0, 11.0, 12.0])
        self.assertEqual([v for _, v in st["wall"]], [90.0, 135.0], "COMMLOST writes no wall reading, never 0")


class Pricing(Isolated):
    CAL = {"a": 50.0, "k_card": 1.2, "c_cpu": None, "idle_cpu_pct": None, "idle_w": 70.0}

    def box(self, wall, card, cpu=None):
        W._LOADED["bighonker"] = {"wall": W.Series(wall), "card": W.Series(card), "cpu": W.Series(cpu) if cpu else None}

    def test_a_covered_window_is_measured(self):
        self.cal(bighonker=self.CAL)
        self.box([(t, 200.0) for t in range(0, 101, 5)], [(t, 100.0) for t in range(0, 101, 5)])
        p = W.price("bighonker", 0, 100, card_j=10000.0, wall_s=100)
        self.assertEqual((p["wall_src"], p["wall_j"], p["wall_extra_j"], p["card_j"]),
                         ("measured", 20000.0, 20000.0 - 70.0 * 100, 10000.0))

    def test_an_uncovered_window_takes_the_line_on_the_telemetrys_card_else_the_answers_meter(self):
        self.cal(bighonker=self.CAL)
        self.box([], [(t, 100.0) for t in range(0, 101, 5)])
        p = W.price("bighonker", 0, 100, card_j=1.0, wall_s=100)
        self.assertEqual((p["wall_src"], p["wall_j"]), ("modelled", (50.0 + 1.2 * 100.0) * 100))
        self.box([], [])
        p = W.price("bighonker", 0, 100, card_j=8000.0, wall_s=100)
        self.assertEqual(p["wall_j"], (50.0 + 1.2 * 80.0) * 100, "no telemetry there: the answer's own meter")

    def test_no_window_uses_the_meter_on_the_line_and_cpu_at_idle(self):
        self.cal(mothership={"a": 90.0, "k_card": 1.1, "c_cpu": 0.7, "idle_cpu_pct": 2.0, "idle_w": 98.0})
        p = W.price("mothership", None, None, card_j=3000.0, wall_s=10.0)
        self.assertEqual(p["wall_src"], "modelled (no window)")
        self.assertAlmostEqual(p["wall_j"], (90.0 + 1.1 * 300.0 + 0.7 * 2.0) * 10.0)

    def test_what_cannot_be_priced_is_none_never_zero(self):
        self.cal()
        self.box([], [])
        for p in (W.price("bighonker", 0, 100, None, 100), W.price("rigby", 0, 100, 5.0, 100),
                  W.price("bighonker", None, None, None, None)):
            self.assertIsNone(p["wall_j"])
        os.remove(W.CALIBRATION)
        W._CAL = None
        self.assertIsNone(W.price("bighonker", None, None, 100.0, 10.0)["wall_j"], "no calibration: unpriced, no crash")


class Tablets(unittest.TestCase):
    def test_a_tablet_is_priced_at_its_ports_spec_ceiling(self):
        self.assertEqual(W.TABLET_PORT_W, 2.5, "a USB data port: 500 mA x 5 V")
        self.assertEqual(W.tablet_j(100), 250.0)
        self.assertEqual(W.tablet_j(100, tablets=4), 1000.0, "an ensemble pays every tablet in it")
        self.assertIsNone(W.tablet_j(""), "no wall: unpriced, never 0")


class Rigby(Isolated):
    def test_the_softserve_split_uses_the_placements_own_working_watts(self):
        """One derivation: the x16 era's working extra that menu_wall subtracts from Softserve's APC is the figure the
        menu prices rigby's standard placement at."""
        std = menu_fill.RIGBY_PLACEMENT["Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"]
        era = W.rigby_era(W.dt.datetime.fromisoformat(std["since"]).timestamp())
        self.assertEqual(era["since"], std["since"])
        self.assertEqual(era["work_w"], std["watts"])

    def test_softserves_wall_is_the_apc_minus_rigby_idle_or_working(self):
        from unittest import mock
        # Softserve's meter, as its WALL_BOXES entry has it (a lab setting): its own telemetry, the APC at 9 W per load
        # percent, the tablet head on the same APC subtracted
        spec = {"local": True, "parse": "telemetry", "ups_w_per_pct": 9.0, "subtract_tablet_head": True}
        for p in (mock.patch.dict(W.BOXES, {"softserve": spec}), mock.patch.dict(W.UPS_W_PER_PCT, {"softserve": 9.0})):
            p.start()
            self.addCleanup(p.stop)
        t0 = W.dt.datetime(2026, 10, 28, 12, 0, 0).timestamp()
        with open(os.path.join(W.OUTBOX, "o.results.json"), "w", encoding="utf-8") as f:
            json.dump({"finished": t0 + 20, "wall_s": 10}, f)                # rigby worked t0+10 .. t0+20
        with open(os.path.join(W.SS_TELEM, "gpu_telemetry-20261028.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("ts,power_w,ups_load_pct,ups_status\n")
            for s in range(0, 30):
                f.write("2026-10-28 12:00:%02d,20,30,ONLINE\n" % s)          # 30 % x 9 = 270 W at the APC
        wall = W.load("softserve")["wall"]
        era = W.rigby_era(t0)
        self.assertEqual(wall.window(t0, t0 + 10)[0], 270.0 - era["idle_w"])
        self.assertEqual(wall.window(t0 + 10, t0 + 20)[0], 270.0 - era["idle_w"] - era["work_w"])


if __name__ == "__main__":
    unittest.main()
