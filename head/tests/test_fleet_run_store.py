"""The fleet run-state store is written by the threadpool and read concurrently.
Writes must be atomic (or synchronized) to prevent torn reads and data loss."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import app
import scheduler


class Store(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.path = os.path.join(self.d, "fleet-run-state.json")
        for p in (mock.patch.object(app, "FLEET_RUN_STATE", self.path),
                  mock.patch.object(app, "_FLEET_RUN_LAST", [None], create=True)):   # create: the old writer too
            p.start()
            self.addCleanup(p.stop)

    def test_concurrent_pushes_never_drop_each_others_records(self):
        hosts = ["h%02d" % i for i in range(24)]

        def push(h):
            for k in range(20):
                app._fleet_run_write({"host": h, "name": "%s-%d" % (h, k), "state": "compute"})
        threads = [threading.Thread(target=push, args=(h,)) for h in hosts]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(self.path, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(sorted(d), hosts, "a push read a half-written store and wrote back only itself")
        self.assertEqual(d["h07"]["name"], "h07-19")
        self.assertFalse([x for x in os.listdir(self.d) if x.endswith(".tmp")], "no temp file left behind")

    def test_an_unreadable_store_shows_the_last_good_copy_never_nothing_running(self):
        app._fleet_run_write({"host": "mothership", "name": "a2", "state": "compute"})
        self.assertEqual(app._fleet_run_all()["mothership"]["name"], "a2")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"mothership": {"na')                       # what a read mid-write used to see
        self.assertEqual(app._fleet_run_all()["mothership"]["name"], "a2")

    def test_a_store_never_written_is_honestly_empty(self):
        self.assertEqual(app._fleet_run_all(), {})


class GuardReadsWhatTheWriterWrites(unittest.TestCase):
    """training_busy reads the store the push writes: while pushes land, the guard must keep seeing the busy box. Its
    'unreadable = no verdict' rule is deliberate (test_stated_run_guard) - the atomic write is what removes the race."""

    def test_the_guard_sees_the_busy_box_through_concurrent_pushes(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "fleet-run-state.json")
        with mock.patch.object(app, "FLEET_RUN_STATE", path), \
                mock.patch.object(app, "_FLEET_RUN_LAST", [None], create=True):
            app._fleet_run_write({"host": "mothership", "name": "a2", "state": "compute"})
            stop, misses = threading.Event(), []

            def push():
                k = 0
                while not stop.is_set():
                    app._fleet_run_write({"host": "bighonker", "name": "b%d" % k, "state": "compute"})
                    k += 1
            t = threading.Thread(target=push)
            t.start()
            try:
                for _ in range(400):
                    if not scheduler.training_busy("mothership", path=path)[0]:
                        misses.append(1)
            finally:
                stop.set()
                t.join()
        self.assertEqual(misses, [], "a read landed mid-write and read the busy box as free")


if __name__ == "__main__":
    unittest.main()
