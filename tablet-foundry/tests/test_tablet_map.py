"""Tests for the 'map' compute kind (general parallel tasks on the tablet fleet).

The map kind reuses the sweep's execution spine (batching, work-stealing,
checkpoints) but treats items as opaque strings with no file staging.

These tests cover:
- Desk validation per kind.
- Shared row_key derivation.
- End-to-end map batch path (with mocked network).
- Customer-side counter refusals.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_fulfil

# `tablet_orders` is only available via bind mount in CI (`run-tolkien-suite.sh`).
# Skip locally to avoid confusing ModuleNotFoundError; raise inside container (/app).
try:
    import tablet_orders
except ImportError as _e:                                            # pragma: no cover
    if os.path.isdir("/app"):
        raise
    raise unittest.SkipTest(
        "test_tablet_map needs `tablet_orders`, which lives in the ORCHESTRATOR lane and is "
        "bind-mounted read-only into the tolkien suite by `run-tolkien-suite.sh`. It is not "
        "importable from a bare local run. Run the suite properly:\n"
        "    ssh sundae \"bash /opt/arrstack/run-tolkien-suite.sh\"\n"
        "(original error: %s)" % _e)


def _map_spec(**kw):
    spec = {
        "kind": "map",
        "name": "t-map",
        "payload": "/ssdshared/tabfoundry-jobs/payloads/map_echo.py",
        "items": ["alpha", "beta", "gamma"],
        "control": {"item": "ctl-input", "expect": "echo:ctl-input"},
    }
    spec.update(kw)
    return spec


class MapItemsAreUniqueAndTheControlIsItsOwn(unittest.TestCase):
    """Coverage counts results by item string: a repeated item or a control equal to an item collapses two rows and
    the order ships VOID "1 unaccounted for" (card-proof.20261016-150444: items ping-1, ping-2 + control ping-1)."""

    def test_a_control_equal_to_an_item_is_refused_at_intake(self):
        d = tablet_foundry.spec_defects(_map_spec(items=["ping-1", "ping-2"],
                                                  control={"item": "ping-1", "expect": "echo:ping-1"}))
        self.assertTrue(any("control item must be distinct" in x for x in d), d)

    def test_a_repeated_item_is_refused_at_intake(self):
        d = tablet_foundry.spec_defects(_map_spec(items=["a", "b", "a"]))
        self.assertTrue(any("unique" in x for x in d), d)

    def test_a_distinct_control_and_unique_items_pass(self):
        d = tablet_foundry.spec_defects(_map_spec(items=["ping-1", "ping-2"],
                                                  control={"item": "ping-ctl", "expect": "echo:ping-ctl"}))
        self.assertFalse(any("distinct" in x or "unique" in x for x in d), d)


class MapSpecDefectsTests(unittest.TestCase):

    def test_valid_map_spec_passes(self):
        self.assertEqual(tablet_foundry.spec_defects(_map_spec()), [])

    def test_map_needs_no_staging_fields(self):
        """src_host/src_dir are sweep concepts; demanding them of a map would refuse every map."""
        d = tablet_foundry.spec_defects(_map_spec())
        self.assertFalse(any("src_host" in x or "src_dir" in x for x in d))

    def test_map_without_control_is_refused(self):
        d = tablet_foundry.spec_defects(_map_spec(control=None))
        self.assertTrue(any("POSITIVE CONTROL" in x for x in d))

    def test_map_control_needs_item_and_expect(self):
        d = tablet_foundry.spec_defects(_map_spec(control={"item": "x"}))
        self.assertTrue(any("expect" in x for x in d))

    def test_unknown_kind_is_refused(self):
        d = tablet_foundry.spec_defects(_map_spec(kind="banana"))
        self.assertTrue(any("unknown kind" in x for x in d))

    def test_item_containing_pipe_is_refused(self):
        """'|' is the batch-key separator; an item carrying one would silently split in two."""
        d = tablet_foundry.spec_defects(_map_spec(items=["ok", "bad|item"]))
        self.assertTrue(any("'|'" in x for x in d))

    def test_sweep_validation_unchanged(self):
        """The original dialect keeps its original requirements — no kind field means sweep."""
        d = tablet_foundry.spec_defects({"name": "s", "payload": "p.py"})
        self.assertTrue(any("items_list" in x for x in d))
        self.assertTrue(any("src_host" in x for x in d))


class RowKeyAndValidatorTests(unittest.TestCase):

    def test_row_key_by_kind(self):
        self.assertEqual(tablet_fulfil.row_key({"kind": "map"}), "item")
        self.assertEqual(tablet_fulfil.row_key({}), "file")

    def test_map_control_passes_on_expected_substring(self):
        spec = _map_spec()
        rows = [{"item": "alpha", "result": "echo:alpha"},
                {"item": "ctl-input", "result": "echo:ctl-input"}]
        ok, why = tablet_fulfil.validate_control(spec, rows)
        self.assertTrue(ok, why)

    def test_map_control_fails_on_wrong_output(self):
        spec = _map_spec()
        rows = [{"item": "ctl-input", "result": "something else entirely"}]
        ok, _ = tablet_fulfil.validate_control(spec, rows)
        self.assertFalse(ok)

    def test_map_control_never_measured_fails(self):
        ok, why = tablet_fulfil.validate_control(_map_spec(), [{"item": "alpha"}])
        self.assertFalse(ok)
        self.assertIn("never measured", why)

    def test_completeness_counts_by_item_key(self):
        rows = [{"item": "a"}, {"item": "b"}]
        ok, _ = tablet_fulfil.validate_complete(rows, 2, [], key="item")
        self.assertTrue(ok)
        ok, why = tablet_fulfil.validate_complete(rows, 3, [], key="item")
        self.assertFalse(ok)

    def test_payload_forgetting_item_echo_fails_coverage(self):
        """The map payload contract (every row carries 'item') is ENFORCED by coverage, not
        assumed: rows without the key count as never-measured."""
        rows = [{"result": "echo:a"}, {"result": "echo:b"}]
        ok, _ = tablet_fulfil.validate_complete(rows, 2, [], key="item")
        self.assertFalse(ok)


class MapRunOrderTests(unittest.TestCase):
    """run_order's map path end-to-end with the network mocked: items fan out, the control rides
    along, rows return keyed by item, nothing tries to stage files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p_state = mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp)
        self.p_state.start()

    def tearDown(self):
        self.p_state.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_map_batch_runs_without_staging(self):
        staged = {"called": False}

        def fake_stage_local(spec, names, wd):
            staged["called"] = True
            return True, ""

        def fake_ssh(host, cmd, timeout=0):
            return 0, ""

        def fake_scp(host, src, dst, timeout=0):
            return 0, ""

        def fake_scp_from(host, remote, local, timeout=0):
            wd = os.path.dirname(local)
            with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
                names = [l.strip() for l in f if l.strip()]
            rows = [{"item": n, "result": "echo:%s" % n} for n in names]
            with open(local, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            return 0, ""

        spec = _map_spec()
        with mock.patch.object(tablet_fulfil, "_stage_local", fake_stage_local), \
             mock.patch.object(tablet_fulfil, "_ssh", fake_ssh), \
             mock.patch.object(tablet_fulfil, "_scp", fake_scp), \
             mock.patch.object(tablet_fulfil, "_scp_from", fake_scp_from):
            rows, errors, wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ["nodeA"], on_rows=None)

        self.assertFalse(staged["called"], "map path must not stage files")
        self.assertEqual(errors, {})
        self.assertEqual(expected, 4)                    # 3 items + the control
        self.assertEqual(unclaimed, [])
        ok, why = tablet_fulfil.validate_control(spec, rows)
        self.assertTrue(ok, why)
        ok, why = tablet_fulfil.validate_complete(rows, expected, unclaimed,
                                                  key=tablet_fulfil.row_key(spec))
        self.assertTrue(ok, why)


class BatchHasNoClockTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p_state = mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp)
        self.p_state.start()

    def tearDown(self):
        self.p_state.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, spec):
        run_timeouts = []

        def fake_ssh(host, cmd, timeout=0):
            if "python" in cmd:                       # the batch run itself, not a mkdir/cleanup call
                run_timeouts.append(timeout)
            return 0, ""

        def fake_scp_from(host, remote, local, timeout=0):
            wd = os.path.dirname(local)
            with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
                names = [l.strip() for l in f if l.strip()]
            with open(local, "w", encoding="utf-8") as f:
                json.dump([{"item": n, "result": "echo:%s" % n} for n in names], f)
            return 0, ""
        with mock.patch.object(tablet_fulfil, "_stage_local", lambda s, n, w: (True, "")), \
             mock.patch.object(tablet_fulfil, "_ssh", fake_ssh), \
             mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")), \
             mock.patch.object(tablet_fulfil, "_scp_from", fake_scp_from):
            tablet_fulfil.run_order(spec, ["nodeA"], on_rows=None)
        return run_timeouts

    def test_no_stated_batch_timeout_means_no_clock(self):
        got = self._run(_map_spec())
        self.assertTrue(got, "positive control: the batch run call was seen")
        self.assertEqual(set(got), {None})

    def test_a_stated_batch_timeout_is_still_honoured(self):
        self.assertEqual(set(self._run(_map_spec(batch_timeout=900))), {900})


class TabletOrdersCounterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "payloads"))
        os.makedirs(os.path.join(self.tmp, "inbox"))
        os.makedirs(os.path.join(self.tmp, "outbox"))
        with open(os.path.join(self.tmp, "payloads", "map_echo.py"), "w") as f:
            f.write("# reviewed payload\n")
        self.p = mock.patch.object(tablet_orders, "SHARE", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_submit_writes_a_valid_spec_into_the_inbox(self):
        msg = tablet_orders.submit_map("probe", "map_echo", ["a", "b"], "ctl", "echo:ctl")
        self.assertIn("SUBMITTED", msg)
        files = os.listdir(os.path.join(self.tmp, "inbox"))
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.tmp, "inbox", files[0]), encoding="utf-8") as f:
            spec = json.load(f)
        self.assertEqual(tablet_foundry.spec_defects(spec), [],
                         "the counter must never compose a spec the desk would hold")
        self.assertTrue(spec["payload"].startswith(tablet_orders.FOUNDRY_SHARE),
                        "spec paths must be in the CONSUMING container's dialect")

    def test_unwhitelisted_payload_refused(self):
        msg = tablet_orders.submit_map("x", "evil_payload", ["a"], "c", "e")
        self.assertIn("not in the whitelist", msg)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "inbox")), [])

    def test_missing_control_refused(self):
        msg = tablet_orders.submit_map("x", "map_echo", ["a"], "", "")
        self.assertIn("POSITIVE CONTROL", msg)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "inbox")), [])

    def test_status_reads_shipped_results(self):
        with open(os.path.join(self.tmp, "outbox", "probe.x.results.json"), "w") as f:
            json.dump({"usable": True, "VOID": False, "control_passed": True,
                       "coverage": "3/3 measured", "rows": [1, 2, 3], "wall_s": 12.0}, f)
        s = tablet_orders.order_status("probe")
        self.assertIn("usable=True", s)
        self.assertIn("3/3 measured", s)




if __name__ == "__main__":
    unittest.main()
