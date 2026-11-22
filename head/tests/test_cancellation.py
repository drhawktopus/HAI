"""General query cancellation ('Stop' for all users) — Project #12 cross-cutting feature."""
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import cancellation
import ollama_client


class _FakeStream:
    """Minimal stand-in for httpx.stream(...) context manager."""
    def __init__(self, lines, on_line=None):
        self.lines = lines
        self.on_line = on_line
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def read(self):
        return b""

    def iter_lines(self):
        for ln in self.lines:
            if self.on_line:
                self.on_line()
            yield ln


class RegistryTests(unittest.TestCase):
    def tearDown(self):
        cancellation.unbind()
        cancellation._clear_for_tests()

    def test_begin_cancel_end(self):
        cancellation.begin("a")
        self.assertEqual(cancellation.active_count(), 1)
        self.assertTrue(cancellation.cancel("a"))
        self.assertFalse(cancellation.cancel("nope"))
        cancellation.end("a")
        self.assertEqual(cancellation.active_count(), 0)

    def test_bind_check_and_isolation(self):
        cancellation.begin("a")
        cancellation.bind("a")
        cancellation.check()                  # not cancelled yet -> no raise
        # cancel from a DIFFERENT thread (mirrors /query/cancel)
        threading.Thread(target=lambda: cancellation.cancel("a")).start()
        # let it land
        for _ in range(50):
            if cancellation.is_cancelled():
                break
            import time
            time.sleep(0.01)
        self.assertTrue(cancellation.is_cancelled())
        with self.assertRaises(cancellation.Cancelled):
            cancellation.check()

    def test_unbound_thread_never_cancelled(self):
        cancellation.begin("a")
        cancellation.cancel("a")
        cancellation.unbind()                 # this thread isn't bound to anything
        self.assertFalse(cancellation.is_cancelled())
        cancellation.check()                  # no raise


class ChatCancelTests(unittest.TestCase):
    def tearDown(self):
        cancellation.unbind()
        cancellation._clear_for_tests()

    def test_chat_stops_at_step_boundary(self):
        # event already set -> chat() raises at the very first loop checkpoint, before any HTTP
        cancellation.begin("j"); cancellation.bind("j"); cancellation.cancel("j")
        with self.assertRaises(cancellation.Cancelled):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [])

    def test_chat_once_aborts_mid_stream(self):
        cancellation.begin("j"); cancellation.bind("j")
        # cancel as soon as the first streamed line is read
        fake = _FakeStream([b'{"message":{"content":"part"},"done":false}'],
                           on_line=lambda: cancellation.cancel("j"))
        with patch("ollama_client._open_chat_stream", return_value=fake):
            with self.assertRaises(cancellation.Cancelled):
                ollama_client._chat_once("http://h", "m", [], [], 8192, "5m")

    def test_chat_once_accumulates_when_not_cancelled(self):
        fake = _FakeStream([
            b'{"message":{"content":"Hello "},"done":false}',
            b'{"message":{"content":"world"},"done":true,"done_reason":"stop","eval_count":3}',
        ])
        with patch("ollama_client._open_chat_stream", return_value=fake):
            parsed = ollama_client._chat_once("http://h", "m", [], [], 8192, "5m")
        self.assertEqual(parsed["message"]["content"], "Hello world")
        self.assertEqual(parsed["done_reason"], "stop")


class RunQueryAndEndpointTests(unittest.TestCase):
    def tearDown(self):
        cancellation.unbind()
        cancellation._clear_for_tests()

    def test_run_query_returns_clean_stopped(self):
        import app
        q = app.Q(text="hi", cancel_id="z")
        with patch.object(app, "_run_query_core", side_effect=cancellation.Cancelled()):
            result = app._run_query(q)
        self.assertEqual(result["route"], "cancelled")
        self.assertEqual(result["message"], "Stopped.")
        self.assertFalse(cancellation.is_cancelled())   # unbound in finally

    def test_query_cancel_endpoint(self):
        import app
        cancellation.begin("live1")
        self.assertEqual(app.query_cancel({"cancel_id": "live1"}), {"ok": True, "cancelling": True})
        self.assertEqual(app.query_cancel({"cancel_id": "ghost"}), {"ok": True, "cancelling": False})


class WiringTests(unittest.TestCase):
    APP = Path("app.py").read_text(encoding="utf-8")

    def test_ui_and_endpoint_present(self):
        self.assertIn('id="stopBtn"', self.APP)
        self.assertIn("new AbortController()", self.APP)
        self.assertIn("/query/cancel", self.APP)
        self.assertIn('"type": "meta"', self.APP)
        self.assertIn("cancel_id", self.APP)


class QueryRunRegistryTests(unittest.TestCase):
    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def tearDown(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def test_register_and_status_running(self):
        registry = cancellation.QueryRunRegistry()
        entry = registry.register("id1")
        self.assertEqual(entry.status, "running")
        self.assertIsNone(entry.result)
        self.assertIsNone(entry.error)

    def test_complete_sets_status_and_result(self):
        registry = cancellation.QueryRunRegistry()
        registry.register("id1")
        dummy_result = {"answer": 42}
        registry.complete("id1", dummy_result)
        entry = registry.get("id1")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result, dummy_result)

    def test_fail_sets_status_and_error(self):
        registry = cancellation.QueryRunRegistry()
        registry.register("id2")
        err = ValueError("oops")
        registry.fail("id2", err)
        entry = registry.get("id2")
        self.assertEqual(entry.status, "failed")
        self.assertEqual(entry.error, err)

    def test_get_unknown_returns_none(self):
        registry = cancellation.QueryRunRegistry()
        self.assertIsNone(registry.get("nonexistent"))

    def test_singleton_identity(self):
        r1 = cancellation.QueryRunRegistry()
        r2 = cancellation.QueryRunRegistry()
        self.assertIs(r1, r2)

    def test_thread_safety_concurrent_operations(self):
        """Concurrent register/get/complete calls leave consistent state."""
        registry = cancellation.QueryRunRegistry()
        N = 50
        errors: list[Exception] = []

        def worker(i):
            try:
                cid = f"thread-{i}"
                registry.register(cid)
                entry = registry.get(cid)
                assert entry is not None, f"Entry for {cid} was None after register"
                assert entry.status == "running", f"Expected running, got {entry.status} for {cid}"
                registry.complete(cid, {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Verify every entry completed consistently
        for i in range(N):
            cid = f"thread-{i}"
            entry = registry.get(cid)
            assert entry is not None, f"Missing entry for {cid}"
            assert entry.status == "completed", f"{cid} status={entry.status}"
            assert entry.result == {"i": i}, f"{cid} result mismatch: {entry.result}"

    def test_thread_safety_mixed_lifecycle(self):
        """Mix of register, complete, and fail across threads preserves consistency."""
        registry = cancellation.QueryRunRegistry()
        N = 30
        errors: list[Exception] = []

        def worker(i):
            try:
                cid = f"mix-{i}"
                registry.register(cid)
                entry = registry.get(cid)
                assert entry is not None and entry.status == "running"
                # Alternate complete vs fail
                if i % 2 == 0:
                    registry.complete(cid, {"ok": True})
                else:
                    registry.fail(cid, RuntimeError("deliberate"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        for i in range(N):
            cid = f"mix-{i}"
            entry = registry.get(cid)
            assert entry is not None
            if i % 2 == 0:
                assert entry.status == "completed"
                assert entry.result == {"ok": True}
                assert entry.error is None
            else:
                assert entry.status == "failed"
                assert isinstance(entry.error, RuntimeError)

    def test_created_at_is_datetime(self):
        import datetime as dt
        registry = cancellation.QueryRunRegistry()
        registry.register("ts")
        entry = registry.get("ts")
        self.assertIsInstance(entry.created_at, dt.datetime)

    def test_purge_expired_marks_stale_running_runs(self):
        """Register a run, back-date created_at past GRACE_TTL_SEC, verify purge marks it expired.
        
        NOTE: We control time by manipulating created_at directly rather than mocking
        time.time(), because the production code reads time from the cancellation module's
        import of the time module — patching there works but is fragile.  Modifying
        created_at is simpler and tests the same age-comparison logic without mock pitfalls.
        """
        import config
        registry = cancellation.QueryRunRegistry()
        entry = registry.register("old_run")
        self.assertEqual(entry.status, "running")

        # Simulate the run being registered long ago by back-dating created_at
        ttl = getattr(config, "GRACE_TTL_SEC", 60)
        entry.created_at -= __import__("datetime").timedelta(seconds=ttl + 1)
        registry.purge_expired()

        self.assertEqual(entry.status, "expired")

    def test_purge_expired_skips_recent_runs(self):
        """A run registered within the TTL window is not expired."""
        import config
        registry = cancellation.QueryRunRegistry()
        entry = registry.register("fresh_run")
        count = registry.purge_expired()
        self.assertEqual(count, 0)
        self.assertEqual(entry.status, "running")

    def test_purge_expired_skips_non_running(self):
        """Completed/failed runs are not touched by purge."""
        registry = cancellation.QueryRunRegistry()
        registry.register("done").status = "completed"
        registry.register("broken").status = "failed"
        count = registry.purge_expired(ttl_sec=0)  # even with ttl=0
        self.assertEqual(count, 0)
        self.assertEqual(registry.get("done").status, "completed")
        self.assertEqual(registry.get("broken").status, "failed")

    def test_get_expired_with_result_returns_entry(self):
        """An expired run that completed before expiring returns its result."""
        registry = cancellation.QueryRunRegistry()
        registry.register("exp_done")
        registry.complete("exp_done", {"answer": 99})
        entry = registry.get("exp_done")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result, {"answer": 99})

        # Now expire it (simulate via direct status change; purge_expired does this)
        entry.status = "expired"
        fetched = registry.get("exp_done")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status, "expired")
        self.assertEqual(fetched.result, {"answer": 99})

    def test_get_expired_without_result_returns_none(self):
        """An expired run that never finished returns None from get()."""
        registry = cancellation.QueryRunRegistry()
        registry.register("exp_noresult")
        entry = registry.get("exp_noresult")
        self.assertEqual(entry.status, "running")

        # Expire it while still running and with no result
        entry.status = "expired"
        fetched = registry.get("exp_noresult")
        self.assertIsNone(fetched)

    def test_purge_expired_returns_count(self):
        """purge_expired returns the number of runs marked expired."""
        import config
        registry = cancellation.QueryRunRegistry()
        registry.register("stale1")
        registry.register("stale2")
        registry.register("fresh3")

        ttl = getattr(config, "GRACE_TTL_SEC", 60)
        # Back-date only stale1 and stale2
        registry._store["stale1"].created_at -= __import__("datetime").timedelta(seconds=ttl + 10)
        registry._store["stale2"].created_at -= __import__("datetime").timedelta(seconds=ttl + 5)
        # fresh3 stays recent
        count = registry.purge_expired()

        self.assertEqual(count, 2)
        # Expired runs with no result return None from get(), so inspect _store directly
        self.assertEqual(registry._store["stale1"].status, "expired")
        self.assertEqual(registry._store["stale2"].status, "expired")
        self.assertEqual(registry.get("fresh3").status, "running")

    def test_purge_expired_no_wraps_misuse(self):
        """Adversarial: purge_expired must not rely on mocking time.time().

        The old implementation compared time.time() against created_at.timestamp().
        That was fragile because: (1) patching cancellation.time with wraps=real_time
        is incorrect usage when the original behavior shouldn't be delegated to, and
        (2) mixing float epochs from two sources risks subtle timezone bugs if a naive
        datetime slips in.

        The fixed implementation uses datetime.now(timezone.utc) and timedelta math,
        so this test verifies that a brand-new run whose created_at is NOT back-dated
        survives purge, while one that IS back-dated expires — proving the age
        comparison works through proper datetime arithmetic."""
        import datetime as dt
        registry = cancellation.QueryRunRegistry()

        # Two runs: one stale, one fresh — both controlled via created_at
        stale_entry = registry.register("fresh_but_backdated")
        stale_entry.created_at -= dt.timedelta(seconds=9999)

        fresh_entry = registry.register("truly_fresh")
        # fresh_entry keeps its default now-ish created_at

        count = registry.purge_expired(ttl_sec=60)

        self.assertEqual(count, 1)
        self.assertEqual(stale_entry.status, "expired")
        self.assertEqual(fresh_entry.status, "running")

    def test_purge_expired_handles_naive_created_at(self):
        """If created_at is naive (no tzinfo), treat it as UTC — matching the
        original time.time() semantics where epoch seconds are always UTC-based."""
        import datetime as dt
        registry = cancellation.QueryRunRegistry()

        # Craft a naive datetime far in the past (no tzinfo)
        stale_entry = registry.register("naive_old")
        stale_entry.created_at = dt.datetime.now() - dt.timedelta(seconds=9999)
        # Ensure it's truly naive
        self.assertIsNone(stale_entry.created_at.tzinfo)

        count = registry.purge_expired(ttl_sec=60)
        self.assertEqual(count, 1)
        self.assertEqual(stale_entry.status, "expired")

    def test_purge_expired_utc_created_at(self):
        """Verify that a run registered exactly past the TTL expires, and one
        just within the window does not — using controlled UTC timestamps."""
        import datetime as dt
        registry = cancellation.QueryRunRegistry()

        now = dt.datetime.now(dt.timezone.utc)

        # 70 seconds ago → past 60s TTL → should expire
        old_entry = registry.register("utc_old")
        old_entry.created_at = now - dt.timedelta(seconds=70)

        # 50 seconds ago → within 60s TTL → should survive
        fresh_entry = registry.register("utc_fresh")
        fresh_entry.created_at = now - dt.timedelta(seconds=50)

        count = registry.purge_expired(ttl_sec=60)

        self.assertEqual(count, 1)
        self.assertEqual(old_entry.status, "expired")
        self.assertEqual(fresh_entry.status, "running")

    def test_register_with_user_signature_and_behavior(self):
        """Adversarial evidence: register_with_user(cancel_id, user) exists with
        exactly that (str, str) signature, returns QueryRun, and populates both
        _store[cid] and _user_map[user].  This proves the call in app.py's
        _run_query — registry.register_with_user(cid, q.user) — is safe."""
        import inspect

        registry = cancellation.QueryRunRegistry()

        # (a) Verify the method exists and has the expected parameter names
        sig = inspect.signature(registry.register_with_user)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["cancel_id", "user"],
                         f"register_with_user signature changed: got {params}")

        # (b) Call it with positional (cid, user) — exactly how app.py calls it
        cid = "sig-test-cid"
        user = "sig-test-user"
        entry = registry.register_with_user(cid, user)

        # (c) Returns a QueryRun
        self.assertIsInstance(entry, cancellation.QueryRun)
        self.assertEqual(entry.status, "running")

        # (d) Entry is in _store keyed by cancel_id
        self.assertIn(cid, registry._store)
        self.assertIs(registry._store[cid], entry)

        # (e) Entry is in _user_map keyed by user
        self.assertIn(user, registry._user_map)
        self.assertIn(cid, registry._user_map[user])

        # (f) Wrong arg count raises TypeError — proving the signature is enforced
        with self.assertRaises(TypeError):
            registry.register_with_user("only_one_arg")  # missing 'user'


class PurgeRetiredTests(unittest.TestCase):
    """Acceptance tests for purge_retired() wired through register().

    Tests exercise the production entry point: calling register() triggers
    _purge_on_register() → purge_expired() → purge_retired(), so completed/failed/expired
    entries older than RUN_RETENTION_SEC are permanently deleted from BOTH _store
    and _user_map.  Running entries are NEVER deleted by purge_retired regardless of age.
    """

    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()
        # Re-enable auto-purge for tests that exercise register() → _purge_on_register()
        cancellation.QueryRunRegistry._auto_purge = True

    def tearDown(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def test_old_completed_run_deleted_via_register(self):
        """Complete a run, back-date past retention, register() deletes it.

        The old entry is gone from get(), no longer returned by
        get_latest_for_user(), and its id removed from the user's list.
        """
        import config
        registry = cancellation.QueryRunRegistry()

        # Step 1: register + complete a run for a user
        cid_old = "retired-old-1"
        registry.register_with_user(cid_old, "alice")
        registry.complete(cid_old, {"answer": "done"})

        # Verify it's there
        entry = registry.get(cid_old)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "completed")

        latest = registry.get_latest_for_user("alice")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.created_at, entry.created_at)

        # Step 2: back-date past retention
        retention = getattr(config, "RUN_RETENTION_SEC", 86400)
        registry.age_entry(cid_old, retention + 10)

        # Step 3: register a fresh run → _purge_on_register() calls purge_expired then purge_retired
        cid_new = "retired-new-1"
        registry.register(cid_new)

        # Step 4: old entry is permanently deleted from _store
        self.assertIsNone(registry.get(cid_old),
                          "Old completed run should be deleted from the store")

        # Step 5: get_latest_for_user() no longer returns the old entry
        latest = registry.get_latest_for_user("alice")
        # Only cid_new remains; it wasn't registered with a user so alice's list is empty
        self.assertIsNone(latest,
                          "Old entry removed from user_map → no runs for alice")

    def test_recent_completed_run_survives_register(self):
        """A completed run INSIDE the retention window survives register()."""
        import config
        registry = cancellation.QueryRunRegistry()

        # Register + complete a recent run for a user
        cid_fresh = "retired-fresh-1"
        registry.register_with_user(cid_fresh, "bob")
        registry.complete(cid_fresh, {"answer": "still-good"})

        # Do NOT back-date — it stays within retention
        # Register a fresh run → purge_retired should keep the recent one
        cid_new = "retired-new-2"
        registry.register(cid_new)

        # Recent completed run survives
        entry = registry.get(cid_fresh)
        self.assertIsNotNone(entry, "Recent completed run should survive purge_retired")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["answer"], "still-good")

        # Still reattachable via get_latest_for_user
        latest = registry.get_latest_for_user("bob")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.result["answer"], "still-good")

    def test_old_running_run_expired_not_deleted(self):
        """An old 'running' run is expired by purge_expired, not deleted by purge_retired."""
        import config
        registry = cancellation.QueryRunRegistry()

        # Register a running run for a user, back-date past GRACE_TTL
        cid_running = "retired-running-1"
        registry.register_with_user(cid_running, "carol")

        ttl = getattr(config, "GRACE_TTL_SEC", 1800)
        registry.age_entry(cid_running, ttl + 10)

        # Register a fresh run → _purge_on_register calls purge_expired THEN purge_retired
        cid_new = "retired-new-3"
        registry.register(cid_new)

        # The running run should be EXPIRED (by purge_expired), not DELETED
        self.assertIn(cid_running, registry._store,
                      "Running→expired entry should still be in _store")
        self.assertEqual(registry._store[cid_running].status, "expired",
                         "Old running run should have been expired by purge_expired")

        # The entry key must not be deleted from _user_map either
        self.assertIn(cid_running, registry._user_map.get("carol", []),
                      "Expired entry id should remain in user_map")

    def test_recent_running_run_untouched(self):
        """A recent 'running' run is untouched by both purges."""
        import config
        registry = cancellation.QueryRunRegistry()

        cid_running = "retired-running-2"
        registry.register_with_user(cid_running, "dave")
        self.assertEqual(registry.get(cid_running).status, "running")

        # Register another run → purges fire
        cid_new = "retired-new-4"
        registry.register(cid_new)

        # Recent running run is still alive and running
        entry = registry.get(cid_running)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "running")

    def test_failed_old_run_deleted(self):
        """An old failed run is also deleted by purge_retired."""
        import config
        registry = cancellation.QueryRunRegistry()

        cid_fail = "retired-fail-1"
        registry.register_with_user(cid_fail, "eve")
        registry.fail(cid_fail, RuntimeError("oops"))

        retention = getattr(config, "RUN_RETENTION_SEC", 86400)
        registry.age_entry(cid_fail, retention + 20)

        # Register triggers purge_retired
        registry.register("retired-new-5")

        self.assertIsNone(registry.get(cid_fail),
                          "Old failed run should be deleted")

    def test_old_expired_run_deleted(self):
        """An old 'expired' run (no result, already marked expired) is also purged."""
        import config
        registry = cancellation.QueryRunRegistry()

        cid_exp = "retired-exp-1"
        entry = registry.register(cid_exp)
        # Mark it expired directly (simulates what purge_expired did earlier)
        entry.status = "expired"
        # No result attached

        retention = getattr(config, "RUN_RETENTION_SEC", 86400)
        registry.age_entry(cid_exp, retention + 30)

        # Register triggers purge_retired
        registry.register("retired-new-6")

        self.assertNotIn(cid_exp, registry._store,
                         "Old expired-without-result entry should be deleted from _store")

    def test_purge_retired_removes_ids_from_user_map(self):
        """Verify retired ids are stripped from _user_map lists."""
        import config
        registry = cancellation.QueryRunRegistry()

        cid1 = "retired-map-1"
        cid2 = "retired-map-2"
        registry.register_with_user(cid1, "frank")
        registry.register_with_user(cid2, "frank")
        registry.complete(cid1, {"v": 1})
        registry.complete(cid2, {"v": 2})

        retention = getattr(config, "RUN_RETENTION_SEC", 86400)
        # Age only cid1 past retention
        registry.age_entry(cid1, retention + 50)
        # cid2 stays fresh

        # Register triggers purge_retired
        registry.register("retired-new-7")

        # Only cid2 should remain in user_map for frank
        uid_list = registry._user_map.get("frank", [])
        self.assertNotIn(cid1, uid_list, "cid1 removed from user_map")
        self.assertIn(cid2, uid_list, "cid2 remains in user_map")

        # And cid2 is still accessible
        entry2 = registry.get(cid2)
        self.assertIsNotNone(entry2)
        self.assertEqual(entry2.result["v"], 2)


class QueryStreamDisconnectTests(unittest.TestCase):
    """Verify that _run_query registers with QueryRunRegistry on completion/failure,
    and that shielding in query_stream prevents client disconnect from killing the thread.
    """

    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def tearDown(self):
        time.sleep(0.2)
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    @patch.object(app, "_run_query_core")
    def test_run_query_registers_complete(self, mock_core):
        """_run_query calls registry.complete() on normal exit."""
        mock_core.return_value = {"route": "normal", "answer": "ok"}

        q = app.Q(text="hello", routing="auto", effort_mode="think_short")
        q.cancel_id = "test-cid-1"
        result = app._run_query(q)

        registry = cancellation.QueryRunRegistry()
        entry = registry.get("test-cid-1")
        self.assertIsNotNone(entry, "Entry should be registered")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["answer"], "ok")
        self.assertEqual(result["route"], "normal")

    @patch.object(app, "_run_query_core")
    def test_run_query_registers_fail_on_exception(self, mock_core):
        """_run_query calls registry.fail() when _run_query_core raises."""
        mock_core.side_effect = RuntimeError("boom")

        q = app.Q(text="fail", routing="auto", effort_mode="think_short")
        q.cancel_id = "test-cid-2"

        with self.assertRaises(RuntimeError):
            app._run_query(q)

        registry = cancellation.QueryRunRegistry()
        entry = registry.get("test-cid-2")
        self.assertIsNotNone(entry, "Entry should be registered even on error")
        self.assertEqual(entry.status, "failed")
        self.assertIsInstance(entry.error, RuntimeError)

    @patch.object(app, "_run_query_core")
    def test_run_query_registers_cancelled(self, mock_core):
        """_run_query calls registry.complete() with cancelled result when Cancelled."""
        mock_core.side_effect = cancellation.Cancelled()

        q = app.Q(text="stop", routing="auto", effort_mode="think_short")
        q.cancel_id = "test-cid-3"
        result = app._run_query(q)

        self.assertEqual(result["route"], "cancelled")

        registry = cancellation.QueryRunRegistry()
        entry = registry.get("test-cid-3")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.result["route"], "cancelled")

    def test_query_stream_uses_shield(self):
        """Verify that query_stream sources contain asyncio.shield call, so client
        disconnect does NOT cancel the background thread."""
        import inspect
        source = inspect.getsource(app.query_stream)
        self.assertIn("asyncio.shield", source,
            "query_stream should shield the background task from cancellation")

    def test_query_stream_does_not_cancel_on_disconnect(self):
        """Verify query_stream's CancelledError/GeneratorExit handler does NOT
        call cancellation.cancel(). It should just pass, letting the thread finish."""
        import inspect
        source = inspect.getsource(app.query_stream)
        # The except block for (CancelledError, GeneratorExit) should NOT contain
        # cancellation.cancel — it should just 'pass' to let the thread complete.
        lines = source.split("\n")
        in_disconnect_handler = False
        handler_lines = []
        brace_depth = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "except (asyncio.CancelledError, GeneratorExit)" in stripped:
                in_disconnect_handler = True
                continue
            if in_disconnect_handler:
                handler_lines.append(stripped)
                # Check if we've left the except block (next non-indented or 'finally')
                if stripped.startswith("finally:") and not line[0].isspace():
                    break

        handler_text = " ".join(handler_lines)
        self.assertNotIn("cancellation.cancel", handler_text,
            "Disconnect handler should NOT cancel the background thread")
        # The worker survives the client disconnect, so the handler must re-raise GeneratorExit
        # and must not await the worker (which would block on the dead connection).
        self.assertIn("_detach_stream_worker(inner, cid)", source,
            "query_stream must adopt the worker task (keep it running + tracked)")
        self.assertIn("raise", handler_text,
            "Disconnect handler must re-raise (GeneratorExit contract for async generators)")
        self.assertNotIn("await inner", handler_text,
            "Disconnect handler must not block on the worker from a dead connection")

    @patch.object(app, "_run_query_core")
    def test_shield_keeps_thread_alive_after_generator_ends(self, mock_core):
        """Acceptance criterion: mock _run_query_core to sleep, spawn the real
        _run_query in a background thread via asyncio.shield (as query_stream does),
        immediately cancel the awaiting coroutine (simulate client disconnect),
        assert the background thread continues and marks the run as 'completed'
        in the registry."""
        import asyncio

        # Mock core sleeps enough that we can observe the thread still running
        # after the generator is closed
        def slow_core(q, status, diag):
            time.sleep(1.5)
            return {"route": "normal", "answer": "done-after-disconnect"}

        mock_core.side_effect = slow_core

        q = app.Q(text="shield-test", routing="auto", effort_mode="think_short")
        cid = "shield-test-cid"
        q.cancel_id = cid

        cancellation.begin(cid)

        async def run_test():
            # Recreate the exact shielded pattern from query_stream:
            #   inner = asyncio.create_task(asyncio.to_thread(_run_query, q))
            #   task = asyncio.shield(inner)
            state = {"message": "start", "changed": True}
            def set_status(msg):
                state["message"] = msg

            inner = asyncio.create_task(asyncio.to_thread(app._run_query, q, set_status))
            task = asyncio.shield(inner)

            # Now simulate the generator being cancelled (client disconnect).
            # In real code this comes from aclose()/StopAsyncIteration hitting the
            # except (CancelledError, GeneratorExit) handler which does 'pass'.
            # Here we cancel the outer await target directly.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # exactly what query_stream's disconnect handler does

            # inner should NOT have been cancelled thanks to shield.
            # Wait for it to finish its sleep and call registry.complete().
            finished_ok = await asyncio.wait_for(inner, timeout=5)

            # Verify the result came through
            self.assertEqual(finished_ok["answer"], "done-after-disconnect")

            # Verify registry state — the run should be marked completed
            registry = cancellation.QueryRunRegistry()
            entry = registry.get(cid)
            self.assertIsNotNone(entry, "Entry should be registered")
            self.assertEqual(entry.status, "completed",
                "Background thread should have marked run as completed despite generator ending early")
            self.assertEqual(entry.result["answer"], "done-after-disconnect")

        asyncio.run(run_test())
        cancellation.end(cid)


class ReattachTests(unittest.TestCase):
    """Tests for the GET /query/reattach endpoint."""

    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def tearDown(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation.unbind()
        cancellation._clear_for_tests()

    def _mock_completed_run(self, user="U1"):
        """Register a completed run for the given user and return the registry."""
        registry = cancellation.QueryRunRegistry()
        cid = "test-cid-completed"
        entry = registry.register_with_user(cid, user)
        registry.complete(cid, {"answer": "hello from previous"})
        return registry, cid

    def test_reattach_completed_run_returns_200(self):
        """Mock registry returns a 'completed' run for user U1; handler returns 200 with status/result."""
        self._mock_completed_run("U1")
        resp = app.query_reattach(user="U1")
        self.assertEqual(resp["status"], "completed")
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["answer"], "hello from previous")
        self.assertIn("elapsed", resp)

    def test_reattach_nonexistent_user_returns_404(self):
        """Handler for user with no runs returns 404."""
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            app.query_reattach(user="nobody")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_reattach_running_run_returns_status_running(self):
        """Handler for a still-running run returns status 'running'."""
        registry = cancellation.QueryRunRegistry()
        cid = "test-cid-running"
        registry.register_with_user(cid, "U2")
        # Do NOT call complete/fail; it stays in 'running' state.
        resp = app.query_reattach(user="U2")
        self.assertEqual(resp["status"], "running")
        self.assertIn("elapsed", resp)

    def test_reattach_by_cancel_id(self):
        """Handler can look up a specific run by cancel_id."""
        registry = cancellation.QueryRunRegistry()
        cid = "test-cid-specific"
        registry.register_with_user(cid, "U3")
        registry.complete(cid, {"answer": "specific"})
        resp = app.query_reattach(cancel_id=cid)
        self.assertEqual(resp["status"], "completed")
        self.assertEqual(resp["result"]["answer"], "specific")

    def test_reattach_cancel_id_not_found_returns_404(self):
        """Handler with unknown cancel_id returns 404."""
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            app.query_reattach(cancel_id="does-not-exist")
        self.assertEqual(ctx.exception.status_code, 404)


class DetachStreamWorkerTests(unittest.TestCase):
    """Test that a stream with a vanished client adopts its worker task (instead of
    awaiting it), avoiding 'async generator ignored GeneratorExit' and blocked dead
    connections, and cleans up the strong ref + cancel handle when the worker finishes."""

    def test_holds_ref_then_cleans_up_on_success(self):
        import asyncio
        ended = []

        async def scenario():
            async def work():
                await asyncio.sleep(0.01)
                return "ok"
            inner = asyncio.get_running_loop().create_task(work())
            with patch.object(cancellation, "end", side_effect=lambda cid: ended.append(cid)):
                app._detach_stream_worker(inner, "cid-detach-1")
                self.assertIn(inner, app._DETACHED_STREAMS)   # strong ref while running
                await inner
                await asyncio.sleep(0)                        # let the done-callback fire
                self.assertNotIn(inner, app._DETACHED_STREAMS)
        asyncio.run(scenario())
        self.assertEqual(ended, ["cid-detach-1"])             # cancel handle released at the END

    def test_worker_error_still_cleans_up_quietly(self):
        import asyncio
        ended = []

        async def scenario():
            async def work():
                raise RuntimeError("worker blew up (already logged by _run_query)")
            inner = asyncio.get_running_loop().create_task(work())
            with patch.object(cancellation, "end", side_effect=lambda cid: ended.append(cid)):
                app._detach_stream_worker(inner, "cid-detach-2")
                with self.assertRaises(RuntimeError):
                    await inner
                await asyncio.sleep(0)
                self.assertNotIn(inner, app._DETACHED_STREAMS)
        asyncio.run(scenario())                               # no exception escapes the callback
        self.assertEqual(ended, ["cid-detach-2"])


if __name__ == "__main__":
    unittest.main()
