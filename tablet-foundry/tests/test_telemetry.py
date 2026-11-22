"""Tolkien telemetry logger: the parsers that turn /proc, nvidia-smi and a tablet's adb sample into rows. No process,
socket or tablet is touched."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tolkien_telemetry as tt

NET = ("Inter-|   Receive                                                |  Transmit\n"
       " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
       "    lo:  100 1 0 0 0 0 0 0  100 1 0 0 0 0 0 0\n"
       "rndis0: 5242880 10 0 0 0 0 0 0 1048576 5 0 0 0 0 0 0\n"
       " wlan0: 999 1 0 0 0 0 0 0 888 1 0 0 0 0 0 0\n")


def stat(utime, stime=0, name="ggml-rpc-server"):
    return "4242 (%s) S " % name + " ".join(["0"] * 10 + [str(utime), str(stime)] + ["0"] * 5)


class Parsers(unittest.TestCase):
    def test_stat_ticks_survive_a_paren_in_the_name(self):
        self.assertEqual(tt.stat_ticks(stat(300, 20, name="llama (x)")), 320)

    def test_cpu_lines_busy_and_total(self):
        got = tt.cpu_lines("cpu  10 0 10 70 10 0 0 0\ncpu0 5 0 5 30 0 0 0 0\nintr 1\n")
        self.assertEqual(got["cpu"], (20, 100))
        self.assertEqual(got["cpu0"], (10, 40))

    def test_net_bytes_keeps_only_matching_interfaces(self):
        self.assertEqual(tt.net_bytes(NET, r"rndis\d"), {"rndis0": (5242880, 1048576)})

    def test_mem_available(self):
        self.assertEqual(tt.mem_avail_mb("MemTotal: 2000 kB\nMemAvailable:  1048576 kB\n"), 1024.0)
        self.assertIsNone(tt.mem_avail_mb(""))

    def test_nvidia_smi_na_is_blank_not_zero(self):
        self.assertEqual(tt.smi_values("1380, 901, 2505, [N/A], 41\n"), [1380.0, 901.0, 2505.0, None, 41.0])

    def test_two_gpus_are_read_by_name_not_by_line(self):
        out = ("NVIDIA GeForce GTX 1060 6GB, 0, 442, 4006, 23.83, 32\n"
               "NVIDIA GeForce GT 730, 0, [N/A], [N/A], [N/A], 39\n")
        rows = tt.smi_rows(out)
        self.assertEqual(rows["NVIDIA GeForce GT 730"], [0.0, None, None, None, 39.0])
        self.assertEqual(rows["NVIDIA GeForce GTX 1060 6GB"], [0.0, 442.0, 4006.0, 23.83, 32.0])
        self.assertEqual(tt.gpu_node("NVIDIA GeForce GTX 1060 6GB"), "gtx10606gb")
        self.assertEqual(tt.smi_rows(""), {})

    def test_a_tablet_sample(self):
        text = ("12345.67 999.0\nP 4242 ggml-rpc-server %s\nMemAvailable:   524288 kB\nT 45000\n%s"
                % (stat(500, 100), NET))
        s = tt.parse_tablet(text)
        self.assertEqual(s["uptime"], 12345.67)
        self.assertEqual(s["procs"], {4242: ("ggml-rpc-server", 600)})
        self.assertEqual((s["mem"], s["temp"]), (512.0, 45.0))
        self.assertEqual(set(s["net"]), {"rndis0", "wlan0"})

    def test_an_unreachable_tablet_has_no_uptime(self):
        self.assertIsNone(tt.parse_tablet("")["uptime"])


class Batching(unittest.TestCase):

    def _stream(self, rows_per_s, run_s):
        import queue as _q
        t = [0.0]

        class Steady(object):
            def __init__(self):
                self.n = 0

            def get(self, timeout=None):
                step = 1.0 / rows_per_s
                if t[0] + step > run_s:
                    t[0] += timeout or 0
                    raise _q.Empty
                t[0] += step
                self.n += 1
                return {"i": self.n}
        return Steady(), (lambda: t[0])

    def test_a_steady_stream_is_still_flushed_within_the_second(self):
        q, clock = self._stream(rows_per_s=130, run_s=60)
        rows = tt.drain_batch(q, {"i": 0}, clock=clock)
        self.assertLessEqual(clock(), 1.01, "the batch closes a second after it began, not after a quiet second")
        self.assertGreater(len(rows), 100)
        self.assertLess(len(rows), 2000)

    def test_a_quiet_queue_flushes_at_once(self):
        q, clock = self._stream(rows_per_s=130, run_s=0)
        self.assertEqual(tt.drain_batch(q, {"i": 0}, clock=clock), [{"i": 0}])

    def test_the_cap_still_bounds_a_batch(self):
        q, clock = self._stream(rows_per_s=100000, run_s=60)
        self.assertEqual(len(tt.drain_batch(q, {"i": 0}, clock=clock, cap=2000)), 2000)


class StatedRun(unittest.TestCase):
    def test_the_run_comes_from_tolkiens_own_pointers(self):
        d = tempfile.mkdtemp()
        fleet, rigby = os.path.join(d, "f.json"), os.path.join(d, "r.json")
        with open(fleet, "w") as f:
            f.write('{"name": "bench-36-x"}')
        with mock.patch.object(tt, "CURRENT_FILES", (("fleet", fleet), ("rigby", rigby))):
            self.assertEqual(tt.stated_run(), "fleet:bench-36-x")


class Store(unittest.TestCase):
    """Searchable, not a log: reads are answers (a line per node), never raw rows."""

    def setUp(self):
        self.db = tt.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite"))

    def put(self, ts, node, **kw):
        row = dict(ts=ts, node=node, run="fleet:bench-36-x", **kw)
        self.db.execute("INSERT INTO samples (%s) VALUES (%s)" % (", ".join(row), ", ".join("?" * len(row))),
                        list(row.values()))

    def test_the_schema_has_exactly_the_declared_columns_and_indexes(self):
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(samples)")]
        self.assertEqual(cols, tt.COLUMNS)
        idx = {r[1] for r in self.db.execute("PRAGMA index_list(samples)")}
        self.assertEqual(idx, {"s_run_ts", "s_node_ts", "s_ts"})

    def test_in_turn_stages_read_one_busy_at_a_time(self):
        for i, n in enumerate(("gandalf", "frodo", "gimli")):
            self.put(1000.0 + 4 * i, n, proc="ggml-rpc-server", proc_cpu_pct=760.0)
            for other in {"gandalf", "frodo", "gimli"} - {n}:
                self.put(1000.0 + 4 * i, other, proc="ggml-rpc-server", proc_cpu_pct=0.0)
        self.assertEqual(tt.overlap(self.db, "bench-36-x"), (1, 3))

    def test_overlapping_stages_read_several_busy_at_once(self):
        for n in ("gandalf", "frodo", "gimli"):
            self.put(1000.0, n, proc="ggml-rpc-server", proc_cpu_pct=700.0)
        self.put(1000.5, "rigby", proc="ggml-rpc-server", proc_cpu_pct=380.0)
        self.assertEqual(tt.overlap(self.db, "bench-36-x")[0], 4)

    def test_loading_is_not_busy(self):
        self.put(1000.0, "gandalf", proc="ggml-rpc-server", proc_cpu_pct=50.0)
        self.assertEqual(tt.overlap(self.db, "bench-36-x"), (0, 0))

    def test_summary_is_a_line_per_node_and_a_null_stays_unread(self):
        self.put(1000.0, "gandalf", proc="ggml-rpc-server", proc_cpu_pct=700.0)
        self.put(1001.0, "gandalf", mem_avail_mb=600.0, temp_c=41.0)
        self.put(1001.0, "rigby", cpu_total_pct=55.0, gpu_mem_mb=1380.0)
        lines = tt.summary(self.db, "bench-36-x")
        self.assertEqual(len(lines), 3)
        self.assertIn("max 1", lines[0])
        self.assertIn("gandalf", lines[1])
        self.assertIn("card mem max 1380 MiB", lines[2])
        self.assertIn("llama cpu mean -", lines[2], "rigby ran no llama process here: unread, not 0")

    def test_an_unknown_run_says_so(self):
        self.assertEqual(tt.summary(self.db, "nope"), ["no samples for run ~nope"])


class RigbyPushesItsLoadReport(unittest.TestCase):
    """rigby runs the GPU boxes' own gpu_load_reporter.py every minute, so a rigby window being opened is a status
    Kirk's clock can see. The reporter is the GPU mirror's file, baked into the image."""

    def run_loop(self, results):
        import threading
        stop, calls, said = threading.Event(), [], []

        def run(argv, **kw):
            calls.append((argv, kw.get("env", {}).get("HAI_URL")))
            if len(calls) >= len(results):
                stop.set()
            r = results[len(calls) - 1]
            if isinstance(r, Exception):
                raise r
            return mock.Mock(stdout=r, stderr="", returncode=0)
        tt.load_report_loop(stop, run=run, period=0.01, log=said.append)
        return calls, said

    def test_a_report_without_a_card_sample_is_said_stale_never_pushed(self):
        calls, said = self.run_loop(['reported {"host": "rigby", "util_pct": null, "power_w": null} -> {"ok": true}'])
        self.assertTrue(said[0].startswith("telemetry: rigby load report pushed but STALE"), said)

    def test_the_reporter_reads_the_1060_under_the_host_loader(self):
        import tempfile
        listing = ("GPU 0: NVIDIA GeForce GTX 1060 6GB (UUID: GPU-fea0d3ed-8dd2)\n"
                   "GPU 1: NVIDIA GeForce GT 730 (UUID: GPU-6dae2e65-805f)\n")
        uuid = tt.load_gpu_uuid(listing)
        self.assertEqual(uuid, "GPU-fea0d3ed-8dd2", "the 1060 by UUID, whatever its index")
        with tempfile.TemporaryDirectory() as d:
            with open(tt.nvidia_wrapper(d, uuid), encoding="utf-8") as f:
                script = f.read()
        self.assertIn("ld-linux-x86-64.so.2", script)
        self.assertIn('-i "GPU-fea0d3ed-8dd2"', script)

    def test_the_reporter_runs_as_rigby_against_kirks_address(self):
        calls, said = self.run_loop(["reported {...} -> {\"ok\": true}"])
        argv, orch = calls[0]
        self.assertEqual((os.path.basename(argv[1]), argv[2]), ("gpu_load_reporter.py", "rigby"))
        self.assertEqual(orch, tt.tf.HAI_URL.rstrip("/"))
        self.assertEqual(said, ["telemetry: rigby load report pushed every 0 s"])

    def test_a_failed_push_is_said_once_per_change_and_never_stops_the_loop(self):
        calls, said = self.run_loop(["report FAILED (URLError) - local sample was {}", "report FAILED (URLError) - local "
                                     "sample was {}", OSError("no python"), "reported {} -> {}"])
        self.assertEqual(len(calls), 4, "every period runs, whatever the last one did")
        self.assertEqual(len(said), 3, "a repeated failure is said once")
        self.assertIn("load report not run", said[1])

    def test_the_reporter_is_baked_beside_this_module_and_runs(self):
        """The property, not the Dockerfile's spelling: the file the loop runs is in the image beside this module, and it
        is the reporter (it compiles, pushes to /fleet/gpu-load and takes the host name from its argument)."""
        import ast
        self.assertTrue(os.path.isfile(tt.REPORTER), "%s is not in the image" % tt.REPORTER)
        with open(tt.REPORTER, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        self.assertIn("main", [n.name for n in tree.body if isinstance(n, ast.FunctionDef)])
        self.assertIn("/fleet/gpu-load", src)
        self.assertIn("sys.argv[1]", src)


if __name__ == "__main__":
    unittest.main()
