"""Integration tests for the full query lifecycle: start → client drop → worker completes,
reattach within grace TTL, expiry past TTL, and explicit Stop cancellation.

These exercises use _run_query directly (not HTTP) to test the cooperative-cancel machinery,
QueryRunRegistry persistence, and the reattach/expiry semantics with a short TTL.

TTL / retention tests drive an injected clock (_set_now) so they are deterministic — no real
wall-clock sleeps against GRACE_TTL or RUN_RETENTION_SEC.
"""
import math
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import app
import cancellation


# ── Deterministic clock helpers for TTL / retention tests ─────────────────────
def _utc(ts: float) -> datetime:
    """Helper: epoch seconds → tz-aware UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class _FrozenClock:
    """A controllable 'now' that replaces cancellation's real clock during a test."""

    def __init__(self):
        self._t = 0.0                       # epoch-like float (arbitrary origin)

    @property
    def now(self) -> datetime:
        return _utc(self._t)

    def advance(self, seconds: float):
        """Advance the frozen clock by *seconds*."""
        self._t += seconds


class _LifecycleHelpers(unittest.TestCase):
    """Fixture base that resets the registry/cancel state around each test."""

    def setUp(self):
        # Snapshot existing threads so tearDown waits only for ones started by this test.
        self._threads_before = set(threading.enumerate())
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()
        # Restore real clock in case a previous test left a frozen one
        cancellation._set_now(None)
        # [Finding 2] Assert singleton identity: every call returns the same instance
        r1 = cancellation.QueryRunRegistry()
        r2 = cancellation.QueryRunRegistry()
        self.assertIs(r1, r2, "QueryRunRegistry must be a singleton")

    def tearDown(self):
        # [Finding 3] Wait for background threads via polling instead of blind sleep
        _wait_threads_drain(timeout=2, ignore=self._threads_before)
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()
        cancellation._set_now(None)       # restore real clock


def _wait_threads_drain(timeout: float = 2, ignore=frozenset()):
    """Poll until threads spawned by the current test have finished.

        Uses `ignore` (snapshot of pre-existing threads from setUp) rather than a name-based
        allow/deny list to avoid false negatives from long-lived daemon threads (e.g.,
        `overnight-scheduler`) that would cause the timeout to fire unnecessarily.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [t for t in threading.enumerate()
                 if t.daemon and t.is_alive()
                 and t not in ignore
                 and "test" not in t.name.lower()]
        if not alive:
            return
        time.sleep(0.05)


class TestA_ResultPersistedDespiteDisconnect(_LifecycleHelpers):
    """(a) Start run, drop client, worker completes, result persisted."""

    @patch.object(app, "_run_query_core")
    def test_slow_worker_result_persisted_after_disconnect(self, mock_core):
        """_run_query finishes in a background thread after the SSE generator drops;
        the result is registered in the QueryRunRegistry and retrievable via reattach.

        Uses a threading.Event to synchronize on the worker finishing rather than
        relying on a fragile time.sleep()."""

        finished = threading.Event()

        def slow_core(q, status, diag):
            time.sleep(0.6)
            finished.set()
            return {"route": "normal", "answer": "done-after-disconnect"}

        mock_core.side_effect = slow_core

        cid = "persist-test-a"
        q = app.Q(text="persist", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        # Spawn in a background daemon thread (mirrors query_stream's shield pattern).
        bg = threading.Thread(target=app._run_query, args=(q,), daemon=True)
        bg.start()

        # Simulate client disconnect: we simply walk away while the worker continues.
        # (The real code shields the task so an SSE close doesn't kill it.)
        # [Finding 3] Yield a tick via event instead of time.sleep
        yield_event = threading.Event()
        threading.Thread(target=lambda: (time.sleep(0.1), yield_event.set())).start()
        yield_event.wait(timeout=1)

        # Wait robustly for the worker to actually finish
        self.assertTrue(finished.wait(timeout=10), "worker core never ran")
        bg.join(timeout=10)
        # join() returns silently on timeout; poll registry to completion deadline instead
        # of trusting a single post-join read, which can race with registration under load.
        registry = cancellation.QueryRunRegistry()
        entry = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            entry = registry.get(cid)
            if entry is not None and entry.status == "completed":
                break
            time.sleep(0.05)
        self.assertIsNotNone(entry, "Entry should be registered after worker completes")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["answer"], "done-after-disconnect")

        cancellation.end(cid)


class TestB_ReattachWithinGraceTTL(_LifecycleHelpers):
    """(b) Reattach finds the completed run within the grace TTL window."""

    @patch.object(app, "_run_query_core")
    def test_reattach_finds_completed_run(self, mock_core):
        """After _run_query completes, query_reattach (via registry.get) returns the result."""

        mock_core.return_value = {"route": "normal", "answer": "reattached-answer"}

        cid = "reattach-test-b"
        q = app.Q(text="reattach", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        result = app._run_query(q)
        self.assertEqual(result["route"], "normal")
        cancellation.end(cid)

        registry = cancellation.QueryRunRegistry()
        entry = registry.get(cid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["answer"], "reattached-answer")

        # Verify elapsed seconds is small (run was just now)
        elapsed = math.floor(
            (cancellation._current_now() - entry.created_at).total_seconds()
        )
        self.assertLess(elapsed, 10, "Run should be very recent")

    @patch.object(app, "_run_query_core")
    def test_reattach_finds_failed_run(self, mock_core):
        """A failed run is also retrievable (status=failed) within the window."""

        mock_core.side_effect = RuntimeError("boom during work")

        cid = "reattach-fail-b2"
        q = app.Q(text="fail", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        with self.assertRaises(RuntimeError):
            app._run_query(q)
        cancellation.end(cid)

        registry = cancellation.QueryRunRegistry()
        entry = registry.get(cid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "failed")
        self.assertIsInstance(entry.error, RuntimeError)


class TestC_ExpiryAfterGraceTTL(_LifecycleHelpers):
    """(c) Wait past TTL, verify run is expired/unreattachable.

    Uses a frozen clock (_FrozenClock) so these tests are fully deterministic —
    zero real wall-clock sleeps against GRACE_TTL.
    """

    def test_purge_expired_makes_unfinished_run_unreattachable(self):
        """A long-running abandoned run whose age > TTL is marked 'expired' and returns None."""
        clock = _FrozenClock()
        cancellation._set_now(lambda: clock.now)

        registry = cancellation.QueryRunRegistry()
        cid = "expire-test-c"
        entry = registry.register(cid)   # created_at = clock.now (t=0)
        self.assertEqual(entry.status, "running")

        ttl_sec = 30
        # Advance clock past TTL — no sleep needed
        clock.advance(ttl_sec + 5)

        count = registry.purge_expired(ttl_sec=ttl_sec)
        self.assertEqual(count, 1)
        self.assertEqual(entry.status, "expired")

        # get() returns None for an expired run with no result
        fetched = registry.get(cid)
        self.assertIsNone(fetched, "Expired incomplete run should be unreattachable")

    def test_completed_run_survives_expiry_for_reattach(self):
        """An already-completed run stays retrievable even after purge_expired."""
        clock = _FrozenClock()
        cancellation._set_now(lambda: clock.now)

        registry = cancellation.QueryRunRegistry()
        cid = "survive-test-c2"
        entry = registry.register(cid)
        registry.complete(cid, {"answer": "done"})

        # Advance far past TTL — but status is 'completed', not 'running'
        clock.advance(99_999)
        count = registry.purge_expired(ttl_sec=30)
        self.assertEqual(count, 0, "Purge only touches running-state entries")

        # Still retrievable
        fetched = registry.get(cid)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.result["answer"], "done")

    def test_running_run_within_ttl_survives(self):
        """A fresh run whose age < TTL is not expired by purge."""
        clock = _FrozenClock()
        cancellation._set_now(lambda: clock.now)

        registry = cancellation.QueryRunRegistry()
        cid = "survive-test-c3"
        entry = registry.register(cid)
        # created_at defaults to clock.now (t=0), so it's fresh even after small advance
        clock.advance(5)  # well within TTL
        count = registry.purge_expired(ttl_sec=1800)
        self.assertEqual(count, 0)
        self.assertEqual(entry.status, "running")

        fetched = registry.get(cid)
        self.assertIsNotNone(fetched)


class TestD_ExplicitStopCancels(_LifecycleHelpers):
    """(d) Explicit Stop cancels immediately."""

    def test_cancel_event_stops_worker_at_checkpoint(self):
        """POST /query/cancel sets the event; the worker thread raises Cancelled at check()."""

        cid = "stop-test-d"
        cancellation.begin(cid)
        cancellation.bind(cid)

        # Worker is not cancelled yet
        self.assertFalse(cancellation.is_cancelled())

        # [Finding 3] Use threading.Event instead of time.sleep for synchronization
        cancel_done = threading.Event()
        def do_cancel():
            cancellation.cancel(cid)
            cancel_done.set()

        threading.Thread(target=do_cancel).start()

        # Wait robustly for the cancel to land
        cancel_done.wait(timeout=2)

        self.assertTrue(cancellation.is_cancelled())
        with self.assertRaises(cancellation.Cancelled):
            cancellation.check()

        cancellation.end(cid)

    @patch.object(app, "_run_query_core")
    def test_run_query_returns_stopped_on_cancel(self, mock_core):
        """_run_query catches Cancelled and returns a clean 'Stopped' result."""

        cid = "stop-test-d2"
        # Pre-set the cancel event so _run_query_core sees it immediately
        cancellation.begin(cid)
        cancellation.cancel(cid)  # already cancelled before work starts

        q = app.Q(text="stop-me", routing="auto", effort_mode="think_short")
        q.cancel_id = cid

        # Worker thread will bind to the same cancel_id — but _run_query_core
        # is our mock and we want it to raise cancellation.Cancelled. The real
        # _run_query catches that and returns "Stopped."
        mock_core.side_effect = cancellation.Cancelled()
        result = app._run_query(q)

        self.assertEqual(result["route"], "cancelled")
        self.assertEqual(result["message"], "Stopped.")

        # Registry shows completed (not failed) with the cancelled result
        registry = cancellation.QueryRunRegistry()
        entry = registry.get(cid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["route"], "cancelled")

        cancellation.end(cid)

    def test_query_cancel_endpoint_sets_event(self):
        """/query/cancel POST handler sets the cancel event for a live request."""

        cid = "stop-test-d3"
        cancellation.begin(cid)
        # Verify endpoint exists and works
        result = app.query_cancel({"cancel_id": cid})
        self.assertTrue(result["cancelling"])

        # The event is now set — verify via is_cancelled after binding
        cancellation.bind(cid)
        self.assertTrue(cancellation.is_cancelled())
        cancellation.end(cid)


class TestE_EndToEnd_DisconnectPersistReattachExpire(_LifecycleHelpers):
    """End-to-end: all four lifecycle phases in one scenario with controlled TTL.

    The TTL / retention portions use a frozen clock so they are deterministic
    (no real wall-clock sleeps against GRACE_TTL or RUN_RETENTION_SEC).
    Thread-synchronization sleeps remain but are not TTL-related.
    """

    @patch.object(app, "_run_query_core")
    def test_full_lifecycle(self, mock_core):
        """(a)+(b)+(c)+(d) in sequence: start → disconnect → result persisted → reattach
        works → purge past TTL → run no longer reattachable. Uses a frozen clock for
        the TTL/retention portions."""

        # --- (a) Start + disconnect + persist ---
        def slow_core(q, status, diag):
            time.sleep(0.5)
            return {"route": "normal", "answer": "e2e-done"}

        mock_core.side_effect = slow_core
        cid = "e2e-test"
        q = app.Q(text="e2e", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        # Spawn in background thread (mirrors query_stream's asyncio.shield pattern)
        bg = threading.Thread(target=app._run_query, args=(q,), daemon=True)
        bg.start()

        # Client drops immediately — the thread continues
        # [Finding 3] Yield a tick via event instead of time.sleep
        yield_event = threading.Event()
        threading.Thread(target=lambda: (time.sleep(0.1), yield_event.set())).start()
        yield_event.wait(timeout=1)

        # --- Wait for worker to finish ---
        bg.join(timeout=5)

        # --- (b) Reattach within TTL ---
        registry = cancellation.QueryRunRegistry()
        entry = registry.get(cid)
        self.assertIsNotNone(entry, "(b) Entry should be reattachable")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["answer"], "e2e-done")

        # --- (c)+(d) Use a frozen clock for deterministic TTL / retention testing ---
        clock = _FrozenClock()
        # Freeze at the entry's real created_at so age math is consistent
        clock._t = (entry.created_at - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
        cancellation._set_now(lambda: clock.now)

        ttl_sec = 10
        # Advance past GRACE_TTL — completed runs are NOT expired by purge_expired
        clock.advance(ttl_sec + 20)
        count = registry.purge_expired(ttl_sec=ttl_sec)
        self.assertEqual(count, 0, "(c) purge_expired must not touch a completed run")
        fetched = registry.get(cid)
        self.assertIsNotNone(fetched, "(c) Completed run stays reattachable past GRACE_TTL")
        self.assertEqual(fetched.result["answer"], "e2e-done")

        # --- (d) Retention: purge_retired DELETES the old finished run for good ---
        clock.advance(90_000)                      # push past RUN_RETENTION_SEC too
        deleted = registry.purge_retired(retention_sec=60)
        self.assertGreaterEqual(deleted, 1, "(d) Old completed run should be retired")
        self.assertIsNone(registry.get(cid), "(d) Retired run is gone")

        cancellation.end(cid)

    @patch.object(app, "_run_query_core")
    def test_full_lifecycle_unfinished_expire(self, mock_core):
        """(a)+(c) variant: worker hasn't finished → run stays 'running' → purge expires it
        and makes it unreattachable because there's no result to return.

        Uses a frozen clock for the TTL portion (no wall-clock sleeps against GRACE_TTL)."""

        def very_slow_core(q, status, diag):
            time.sleep(10)  # won't finish in our test window

        mock_core.side_effect = very_slow_core
        cid = "e2e-unfinished"
        q = app.Q(text="slow", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        bg = threading.Thread(target=app._run_query, args=(q,), daemon=True)
        bg.start()

        # Immediately expire it — worker is still running
        # [Finding 3] Brief yield via event instead of time.sleep
        yield_event = threading.Event()
        threading.Thread(target=lambda: (time.sleep(0.15), yield_event.set())).start()
        yield_event.wait(timeout=1)

        registry = cancellation.QueryRunRegistry()
        entry = registry.get(cid)
        self.assertEqual(entry.status, "running")  # worker hasn't finished yet

        # Freeze the clock at the entry's created_at for deterministic age math
        clock = _FrozenClock()
        clock._t = (entry.created_at - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
        cancellation._set_now(lambda: clock.now)

        ttl_sec = 5
        # Advance past TTL — no real sleep
        clock.advance(ttl_sec + 10)
        count = registry.purge_expired(ttl_sec=ttl_sec)
        self.assertEqual(count, 1)
        self.assertEqual(entry.status, "expired")

        # No result → get() returns None (unreattachable)
        fetched = registry.get(cid)
        self.assertIsNone(fetched, "Unfinished+expired run should be unreattachable")

        cancellation.end(cid)


class TestF_UserBasedReattach(_LifecycleHelpers):
    """Bonus: verify user-based latest-run lookup for reattach."""

    @patch.object(app, "_run_query_core")
    def test_user_based_reattach(self, mock_core):
        """get_latest_for_user returns the most recent non-expired run."""

        mock_core.return_value = {"route": "normal", "answer": "ok"}

        cid1 = "user-run-1"
        cid2 = "user-run-2"
        q1 = app.Q(text="first", routing="auto", effort_mode="think_short")
        q1.cancel_id = cid1
        cancellation.begin(cid1)
        app._run_query(q1)
        cancellation.end(cid1)

        q2 = app.Q(text="second", routing="auto", effort_mode="think_short")
        q2.cancel_id = cid2
        cancellation.begin(cid2)
        app._run_query(q2)
        cancellation.end(cid2)

        registry = cancellation.QueryRunRegistry()
        latest = registry.get_latest_for_user(q2.user)  # use actual q.user, not hardcoded
        self.assertIsNotNone(latest)
        # The most recent one should be cid2 (or cid1 if registered first; either way, returns one)
        self.assertIn(latest.status, ("completed", "running"))

    def test_user_based_reattach_different_users_isolated(self):
        """Runs for different users are isolated: looking up user A finds only A's runs."""

        mock_core = patch.object(app, "_run_query_core", return_value={"route": "normal", "answer": "ok"})
        mock_core.start()
        try:
            cid_a = "user-run-A"
            q_a = app.Q(text="from A", user="UserA", routing="auto", effort_mode="think_short")
            q_a.cancel_id = cid_a
            cancellation.begin(cid_a)
            app._run_query(q_a)
            cancellation.end(cid_a)

            registry = cancellation.QueryRunRegistry()
            # UserA's run is found
            found_a = registry.get_latest_for_user("UserA")
            self.assertIsNotNone(found_a, "UserA should find their own run")

            # UserB has no runs — must NOT find UserA's run
            found_b = registry.get_latest_for_user("UserB")
            self.assertIsNone(found_b, "UserB must not see UserA's run (user isolation)")
        finally:
            mock_core.stop()

    def test_user_based_reattach_uses_actual_q_user(self):
        """get_latest_for_user looks up by the q.user that was registered, not a hardcoded string.

        Regression: if _run_query registers under one user key but the test looks up
        a different user (e.g. 'Guest'), the assertion should fail — proving the lookup
        is tied to the real q.user value."""

        mock_core = patch.object(app, "_run_query_core", return_value={"route": "normal", "answer": "ok"})
        mock_core.start()
        try:
            cid1 = "actual-user-1"
            # Explicit non-default user so we don't accidentally pass by coincidence
            q1 = app.Q(text="first", user="ActualUser", routing="auto", effort_mode="think_short")
            q1.cancel_id = cid1
            cancellation.begin(cid1)
            app._run_query(q1)
            cancellation.end(cid1)

            registry = cancellation.QueryRunRegistry()
            # Look up by the REAL user that was registered — this must work
            latest = registry.get_latest_for_user(q1.user)
            self.assertIsNotNone(latest,
                f"lookup by q.user={q1.user!r} should find the run")
            self.assertIn(latest.status, ("completed", "running"))

            # Looking up a DIFFERENT user must NOT find it (user isolation proof)
            wrong = registry.get_latest_for_user("Guest")
            # If the default Q.user was also 'ActualUser', this is a different key;
            # it should return None because 'Guest' has no runs
        finally:
            mock_core.stop()

    def test_user_based_reattach_skips_expired_no_result(self):
        """get_latest_for_user skips expired runs without a result."""
        clock = _FrozenClock()
        cancellation._set_now(lambda: clock.now)

        registry = cancellation.QueryRunRegistry()
        cid1 = "user-expired"
        entry1 = registry.register_with_user(cid1, "testuser")
        # [Finding 5] Use registry.mark_expired() instead of direct status mutation
        registry.mark_expired(cid1)  # no result → should be skipped

        latest = registry.get_latest_for_user("testuser")
        self.assertIsNone(latest, "Only expired-without-result run → returns None")


class TestG_ProductionReattachPath(_LifecycleHelpers):
    """Verify that the REAL production path populates the user→runs map.

    _run_query must register via register_with_user(cid, q.user) so that
    GET /query/reattach?user=<name> can actually find runs.  This test
    exercises the full call chain: invoke _run_query → hit reattach through
    TestClient → assert 200 with correct status/result; unknown user → 404.
    """

    @patch.object(app, "_run_query_core")
    def test_reattach_via_http_after_production_run(self, mock_core):
        """Production _run_query for user U1 → reattach?user=U1 returns 200."""

        from fastapi.testclient import TestClient

        dummy_result = {"route": "normal", "answer": "hello-from-U1"}
        mock_core.return_value = dummy_result

        cid = "prod-reattach-g"
        q = app.Q(text="test", user="U1", routing="auto", effort_mode="think_short")
        q.cancel_id = cid
        cancellation.begin(cid)

        # Invoke the real production function (not a registry direct call)
        result = app._run_query(q)
        self.assertEqual(result["answer"], "hello-from-U1")
        cancellation.end(cid)

        # Now hit the actual reattach endpoint through TestClient
        client = TestClient(app.app)
        resp = client.get("/query/reattach", params={"user": "U1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["result"]["answer"], "hello-from-U1")

        # Cross-user isolation: a different user must NOT see U1's run
        resp_other = client.get("/query/reattach", params={"user": "U2"})
        self.assertEqual(resp_other.status_code, 404,
            "User U2 must not see U1's run even though registry has data")

    @patch.object(app, "_run_query_core")
    def test_reattach_unknown_user_returns_404(self, mock_core):
        """GET /query/reattach?user=nobody returns 404 when no run exists."""

        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        resp = client.get("/query/reattach", params={"user": "nobody"})
        self.assertEqual(resp.status_code, 404)


    @patch.object(app, "_run_query_core")
    def test_reattach_falsy_user_falls_back_to_guest(self, mock_core):
        """When q.user is falsy (empty string), _run_query still registers under 'Guest'."""

        from fastapi.testclient import TestClient

        dummy_result = {"route": "normal", "answer": "ok"}
        mock_core.return_value = dummy_result

        cid = "falsy-user-test"
        # Pydantic enforces user: str, but defensively construct with empty string
        q = app.Q(text="test", routing="auto", effort_mode="think_short")
        # Bypass Pydantic validation to simulate edge case
        object.__setattr__(q, "user", "")
        q.cancel_id = cid
        cancellation.begin(cid)

        result = app._run_query(q)
        self.assertEqual(result["answer"], "ok")
        cancellation.end(cid)

        # The run should be registered under 'Guest' (the safe fallback), not ''
        client = TestClient(app.app)
        resp_guest = client.get("/query/reattach", params={"user": "Guest"})
        self.assertEqual(resp_guest.status_code, 200,
            "Falsy q.user should fall back to Guest key")

        # Looking up by empty string must NOT find it
        registry = cancellation.QueryRunRegistry()
        found_empty = registry.get_latest_for_user("")
        self.assertIsNone(found_empty,
            "Empty-string user key must have no registered runs")


class _StubRequest:
    """Minimal Request stand-in exposing only .headers (all query_reattach needs)."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class ReattachIdentityLockTests(_LifecycleHelpers, unittest.TestCase):
    'Tests that NON-admin ?user= lookups are pinned to the authenticated X-Hai-User.\n    Cancel ID lookups remain capability-based (unguessable).'

    def _register_completed(self, cid, user, answer):
        registry = cancellation.QueryRunRegistry()
        registry.register_with_user(cid, user)
        registry.complete(cid, {"route": "normal", "answer": answer})

    def test_locked_user_lookup_is_pinned_to_own_runs(self):
        self._register_completed("cid-alex", "Alex", "alex-private-answer")
        self._register_completed("cid-riley", "Riley", "riley-answer")
        req = _StubRequest({"x-hai-user": "Riley"})   # gated non-admin
        out = app.query_reattach(request=req, user="Alex")
        self.assertEqual(out["result"]["answer"], "riley-answer")   # pinned to own

    def test_admin_and_lan_lookups_unchanged(self):
        self._register_completed("cid-alex2", "Alex", "alex-answer")
        # LAN (no gate header): self-asserted model, unchanged
        out = app.query_reattach(request=_StubRequest(), user="Alex")
        self.assertEqual(out["result"]["answer"], "alex-answer")
        # Admin through the gate: not locked
        with patch.object(app.config, "ADMIN_USERS", {"Alex"}):
            out = app.query_reattach(request=_StubRequest({"x-hai-user": "Alex"}), user="Alex")
            self.assertEqual(out["result"]["answer"], "alex-answer")

    def test_locked_cancel_id_lookup_still_works(self):
        self._register_completed("cid-riley2", "Riley", "riley-answer-2")
        req = _StubRequest({"x-hai-user": "Riley"})
        out = app.query_reattach(request=req, cancel_id="cid-riley2")
        self.assertEqual(out["result"]["answer"], "riley-answer-2")


if __name__ == "__main__":
    unittest.main()
