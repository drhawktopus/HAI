"""Tests for kirk_watchdog — watchdog decision logic, command builders, and I/O helpers."""
import json
import os
import tempfile
import unittest

from kirk_watchdog import (
    decide,
    main,
    probe_health,
    read_window,
    build_rollback_cmds,
    build_tag_cmd,
    image_name,
    send_report,
    ssh_prefix,
)

SSH = ssh_prefix("watchdog_key", "watcher@head.example")
STACK = "/opt/stack"


# ---------------------------------------------------------------------------
# read_window
# ---------------------------------------------------------------------------

class TestReadWindow(unittest.TestCase):

    def test_missing_file_returns_none(self):
        self.assertIsNone(read_window("/nonexistent/path/to/window.json"))

    def test_valid_json_returns_dict(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "open": True,
                "reason": "deploy phase 4",
                "opened_at": "2026-08-15T00:00:00Z",
                "deadline_epoch": 1790000000.0,
                "opened_by": "kirk-self-deploy",
            }, f)
            path = f.name
        try:
            result = read_window(path)
            self.assertIsInstance(result, dict)
            self.assertTrue(result["open"])
        finally:
            os.unlink(path)

    def test_bad_json_returns_unreadable(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("this is not json at all {{{")
            path = f.name
        try:
            self.assertEqual(read_window(path), "UNREADABLE")
        finally:
            os.unlink(path)

    def test_three_states_are_distinct(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("broken")
            bad = f.name
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump({"open": False}, f)
                good = f.name
            try:
                miss = read_window("/no/such/file.json")
                readable = read_window(good)
                unreadable = read_window(bad)
                self.assertIsNone(miss)
                self.assertIsInstance(readable, dict)
                self.assertEqual(unreadable, "UNREADABLE")
                # ensure they don't collapse into each other
                self.assertNotEqual(miss, unreadable)
                self.assertNotEqual(readable, unreadable)
            finally:
                os.unlink(good)
        finally:
            os.unlink(bad)


# ---------------------------------------------------------------------------
# probe_health (live-network tests skipped; we test contract via mock-free paths)
# ---------------------------------------------------------------------------

class TestProbeHealth(unittest.TestCase):

    def test_unreachable_host_returns_false(self):
        # Local port that nothing listens on
        self.assertFalse(probe_health("http://127.0.0.1:59999/health", timeout_s=1))


# ---------------------------------------------------------------------------
# decide — core logic (PURE, call with plain dicts)
# ---------------------------------------------------------------------------

class TestDecide(unittest.TestCase):

    # --- heartbeat on every tick ---
    def test_heartbeat_always_present(self):
        actions, _ = decide(None, True, 100.0, {})
        kinds = [a["kind"] for a in actions]
        self.assertIn("heartbeat", kinds)

    # --- rollback fires ONLY when all 4 conditions hold ---
    def test_rollback_requires_all_four_conditions(self):
        now = 200.0
        window = {
            "open": True,
            "deadline_epoch": 100.0,
            "opened_at": "win-1",
        }

        # missing: no window → no rollback
        actions, _ = decide(None, False, now, {})
        self.assertFalse(any(a["kind"] == "rollback" for a in actions))

        # missing: window not open → no rollback
        actions, _ = decide({"open": False}, False, now, {})
        self.assertFalse(any(a["kind"] == "rollback" for a in actions))

        # missing: deadline not passed → no rollback
        actions, _ = decide({**window, "deadline_epoch": 300.0}, False, now, {})
        self.assertFalse(any(a["kind"] == "rollback" for a in actions))

        # missing: probe healthy → no rollback
        actions, _ = decide(window, True, now, {})
        self.assertFalse(any(a["kind"] == "rollback" for a in actions))

        # ALL four conditions met → rollback fires
        actions, _ = decide(window, False, now, {})
        kinds = [a["kind"] for a in actions]
        self.assertIn("rollback", kinds)

    # --- rollback only ONCE per window; new window can fire again ---
    def test_rollback_once_per_window(self):
        now = 200.0
        state = {}
        window_a = {"open": True, "deadline_epoch": 100.0, "opened_at": "win-A"}

        # first tick for win-A → rollback fires
        actions, state = decide(window_a, False, now, state)
        self.assertTrue(any(a["kind"] == "rollback" for a in actions))

        # second tick same window → no rollback (already done)
        actions2, state = decide(window_a, False, now, state)
        self.assertFalse(any(a["kind"] == "rollback" for a in actions2))

        # new window (different opened_at) → rollback fires again
        window_b = {"open": True, "deadline_epoch": 100.0, "opened_at": "win-B"}
        actions3, state = decide(window_b, False, now, state)
        self.assertTrue(any(a["kind"] == "rollback" for a in actions3))

    # --- 3-consecutive-miss report fires at tick 3 only ---
    def test_miss_report_at_tick_3_only(self):
        no_window = None
        state = {}
        now = 100.0

        # tick 1: dead, no window → miss count becomes 1; no report
        a, state = decide(no_window, False, now, state)
        self.assertFalse(any(x["kind"] == "report" for x in a))

        # tick 2: dead → miss count 2; no report
        a, state = decide(no_window, False, now, state)
        self.assertFalse(any(x["kind"] == "report" for x in a))

        # tick 3: dead → miss count 3 → report fires ONCE
        a, state = decide(no_window, False, now, state)
        reports = [x for x in a if x["kind"] == "report"]
        self.assertEqual(len(reports), 1)

        # tick 4: still dead → miss count 4; NO report (already reported)
        a, state = decide(no_window, False, now, state)
        self.assertFalse(any(x["kind"] == "report" for x in a))

    # --- UNREADABLE window → report only, never rollback ---
    def test_unreadable_reports_never_rolls_back(self):
        state = {}
        # even with dead probe past deadline context
        actions, _ = decide("UNREADABLE", False, 200.0, state)
        self.assertFalse(any(a["kind"] == "rollback" for a in actions))
        reports = [a for a in actions if a["kind"] == "report"]
        self.assertTrue(len(reports) >= 1)

    # --- maintain_tag suppressed during any open window ---
    def test_maintain_tag_suppressed_during_open_window(self):
        state = {}
        window = {"open": True, "deadline_epoch": 999.0, "opened_at": "x"}
        actions, _ = decide(window, True, 100.0, state)
        self.assertFalse(any(a["kind"] == "maintain_tag" for a in actions))

    # --- maintain_tag emitted when healthy + no window ---
    def test_maintain_tag_when_healthy_no_window(self):
        state = {}
        actions, _ = decide(None, True, 100.0, state)
        self.assertTrue(any(a["kind"] == "maintain_tag" for a in actions))

    # --- UNREADABLE window + healthy probe: no maintain_tag (fail-closed) ---
    def test_unreadable_no_maintain_tag(self):
        """UNREADABLE is not 'positively absent' — must never become known-good."""
        state = {}
        actions, _ = decide("UNREADABLE", True, 100.0, state)
        self.assertFalse(any(a["kind"] == "maintain_tag" for a in actions))
        # INCONCLUSIVE report still present
        reports = [a for a in actions if a["kind"] == "report"]
        self.assertTrue(len(reports) >= 1)

    # --- forgot-to-close report fires once ---
    def test_forgot_to_close_report_once(self):
        window = {"open": True, "deadline_epoch": 100.0, "opened_at": "win-fc"}
        state = {}

        # past deadline + healthy → report
        a, state = decide(window, True, 200.0, state)
        self.assertTrue(any(x["kind"] == "report" for x in a))

        # same window again → no repeat
        a2, state = decide(window, True, 210.0, state)
        self.assertFalse(any(x["kind"] == "report" for x in a2))

    # --- decide() is pure: calling with plain dicts causes no I/O ---
    def test_decide_purity_plain_dicts(self):
        """Pass only builtins; if decide() tried file/network access it would fail."""
        state = {}
        window = {"open": False, "deadline_epoch": 0.0}
        actions, new_state = decide(window, True, 42.0, state)
        self.assertIsInstance(actions, list)
        self.assertIsInstance(new_state, dict)


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

class TestCommandBuilders(unittest.TestCase):

    def test_rollback_cmds_has_two_commands(self):
        cmds = build_rollback_cmds(SSH, STACK)
        self.assertEqual(len(cmds), 2)

    def test_rollback_first_cmd_tags_known_good_to_latest(self):
        tag_part = build_rollback_cmds(SSH, STACK)[0][-1]
        self.assertIn("stack-orchestrator:known-good", tag_part)
        self.assertIn("stack-orchestrator:latest", tag_part)

    def test_rollback_second_cmd_has_no_build(self):
        """--no-build is LOAD-BEARING; without it compose rebuilds the broken source."""
        compose_part = build_rollback_cmds(SSH, STACK)[1][-1]
        self.assertIn("--no-build", compose_part)

    def test_rollback_cmds_have_batchmode(self):
        for cmd in build_rollback_cmds(SSH, STACK):
            self.assertIn("-o", cmd)
            self.assertIn("BatchMode=yes", cmd)

    def test_tag_cmd_single_command(self):
        cmd = build_tag_cmd(SSH, STACK)
        self.assertIsInstance(cmd, list)
        self.assertIn("docker tag", cmd[-1])
        self.assertIn("known-good", cmd[-1])

    def test_the_image_is_named_after_the_stack_folder_as_compose_names_it(self):
        self.assertEqual(image_name("/opt/stack"), "stack-orchestrator")
        self.assertEqual(image_name("/opt/stack/"), "stack-orchestrator")
        self.assertIn("cd /opt/stack && ", build_rollback_cmds(SSH, STACK)[1][-1])

    def test_tag_cmd_has_batchmode(self):
        cmd = build_tag_cmd(SSH, STACK)
        self.assertIn("-o", cmd)
        self.assertIn("BatchMode=yes", cmd)


# ---------------------------------------------------------------------------
# State round-trip through json.dumps / json.loads
# ---------------------------------------------------------------------------

class TestStateRoundTrip(unittest.TestCase):

    def test_state_survives_json_round_trip(self):
        """Watchdog state must survive json.dumps/json.loads without loss."""
        window = {"open": True, "deadline_epoch": 100.0, "opened_at": "win-A"}
        now = 200.0

        # tick 1: triggers rollback
        actions1, state1 = decide(window, False, now, {})
        self.assertTrue(any(a["kind"] == "rollback" for a in actions1))

        # serialize → deserialize
        dumped = json.dumps(state1)
        loaded = json.loads(dumped)

        # tick 2 with reloaded state: rollback must NOT fire (already done)
        actions2, state2 = decide(window, False, now, loaded)
        self.assertFalse(any(a["kind"] == "rollback" for a in actions2))


# ---------------------------------------------------------------------------
# main() — CLI entry point I/O tests
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def test_main_creates_state_file_on_first_run(self):
        """Fresh install: state file doesn't exist yet, must not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            window_path = os.path.join(tmp, "window.json")
            # Don't create the state file — main should handle missing file
            self.assertFalse(os.path.exists(state_path))
            # Create a valid (empty) window file so read_window doesn't error
            with open(window_path, "w") as f:
                json.dump({"open": False}, f)
            # Call main with a non-existent state file and unreachable URL
            rc = main(["--state", state_path, "--window", window_path,
                       "--url", "http://127.0.0.1:59999/health",
                       "--ssh-key", "watchdog_key", "--ssh-target", "watcher@head.example",
                       "--stack-dir", STACK])
            self.assertEqual(rc, 0)
            # State file should now exist and be valid JSON
            self.assertTrue(os.path.exists(state_path))
            with open(state_path, "r") as f:
                state = json.load(f)
            self.assertIsInstance(state, dict)


class TestReport(unittest.TestCase):
    """A report goes through the lab's relay the way the factory's notify() sends: the relay's address from lab.env,
    its token and the admin's id from dm_relay.env (example values here)."""

    def _etc(self, tmp, settings=True, creds=True):
        if settings:
            with open(os.path.join(tmp, "lab.env"), "w") as f:
                f.write("OVERNIGHT_DM_URL=http://relay.example/dm\nOVERNIGHT_DM_HOST=relay.example\n")
        if creds:
            with open(os.path.join(tmp, "dm_relay.env"), "w") as f:
                f.write("OVERNIGHT_DM_TOKEN=example-token\nOVERNIGHT_DM_USER_ID=example-admin\n")
        return tmp

    def test_the_report_carries_the_admin_id_token_and_host(self):
        sent = []

        class Resp:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            sent.append(req)
            return Resp()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(send_report("probe dead", self._etc(tmp), urlopen=fake), "sent")
        req = sent[0]
        body = json.loads(req.data)
        self.assertEqual(body, {"user_id": "example-admin", "message": "[kirk-watchdog] probe dead"})
        self.assertEqual(req.full_url, "http://relay.example/dm")
        self.assertEqual(req.get_header("X-dm-token"), "example-token")
        self.assertEqual(req.get_header("Host"), "relay.example")

    def test_no_relay_configured_is_said_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("not configured", send_report("x", self._etc(tmp, creds=False)))

    def test_a_relay_that_fails_is_said_not_raised(self):
        def boom(req, timeout=0):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(send_report("x", self._etc(tmp), urlopen=boom), "not sent: OSError")


if __name__ == "__main__":
    unittest.main()
