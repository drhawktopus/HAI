"""Cooperative cancellation for in-flight queries — a general 'stop this response' for ALL users.

Each query runs in its own worker thread (app's `asyncio.to_thread(_run_query)`), so a thread-local cancel
event is the natural carrier: the worker BINDS to its event for the life of the request, and any long-running
code on that thread (the ollama tool loop, the model stream, deliberation passes, tools) calls check() at safe
boundaries. A SEPARATE request thread (`POST /query/cancel`) or a client disconnect sets the same event via
the id->event registry, and the worker observes it at its next checkpoint and unwinds cleanly.

This is the same cooperative-cancel pattern as the Project #12 self-deploy executor, applied to the query path.
"""
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

_LOCK = threading.Lock()
_EVENTS = {}                 # cancel_id -> threading.Event (shared between the worker + the canceller)
_local = threading.local()   # per-worker-thread: the event this request is bound to

# ── Injectable clock for deterministic TTL testing ──────────────────────────────
# Production default: real wall-clock.  Tests can replace _now with a callable that
# returns a controlled datetime, then restore after the test.  No public API param
# changes — callers still use purge_expired(ttl_sec=...) and purge_retired(...).
_now = lambda: datetime.now(timezone.utc)


def _set_now(fn):
    """Override the clock used by purge_expired / purge_retired (test helper).

    *fn* must be a zero-arg callable returning a timezone-aware datetime.
    Call with ``None`` to restore the real clock.
    """
    global _now
    if fn is None:
        _now = lambda: datetime.now(timezone.utc)
    else:
        _now = fn


def _current_now() -> datetime:
    """Return 'now' as a tz-aware UTC datetime, using whatever clock is active."""
    return _now()


@dataclass
class QueryRun:
    """Lifecycle state for a single query run, keyed by cancel_id.

    Fields
    ------
    status : str
        One of "running", "completed", "failed", "expired".
        "expired" means the run was abandoned in-progress past GRACE_TTL_SEC.
    created_at : datetime
        UTC timestamp when the run was registered.
    result : object | None
        Final result payload (set on successful completion).
    error : Exception | str | None
        Error that caused failure, if any.
    user : str
        Who started the run. Already known at `register_with_user` time and previously kept only
        in the registry's user->ids map, which answers "what did this user run?" but not the
        reverse. The "HAI is thinking" tile needs the reverse, so it is recorded here rather than
        by scanning every user's list.
    stage : str
        The most recent human-readable progress message for this run -- verbatim the same string
        the streaming client is shown ("Tool step 3/12: search_docs", "Think Really Hard:
        refining (round 2, 3 finding(s))"). One derivation: the worker already emits these
        through its status callback, so nothing new is computed and the tile can never disagree
        with the chat window. Empty until the first message arrives.
    stage_at : datetime | None
        When `stage` last CHANGED. Note this is deliberately NOT a liveness signal: a worker is
        legitimately silent for minutes inside one long model generation, so an old `stage_at`
        means "still doing the same thing", not "dead". Liveness is the cancel handle.
    """

    status: str = "running"
    created_at: datetime = field(default_factory=_current_now)
    result: object = None
    error: object = None
    user: str = ""
    stage: str = ""
    stage_at: datetime = None


class QueryRunRegistry:
    """Thread-safe singleton registry mapping cancel_id -> QueryRun.

    The global instance is accessible via `QueryRunRegistry.instance`.
    """

    _instance: "QueryRunRegistry | None" = None
    _init_lock = threading.Lock()
    _auto_purge: bool = True       # production default: purge on each register

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._store: dict[str, QueryRun] = {}
                    cls._instance._user_map: dict[str, list[str]] = {}  # user -> [cancel_ids, newest last]
        return cls._instance

    @property
    def instance(self):
        """Convenience property returning the singleton itself."""
        return self

    def register(self, cancel_id: str) -> QueryRun:
        """Register a new run and return its QueryRun.

        Opportunistically purges expired and retired entries so the registry is
        self-maintaining in production with no scheduler.
        """
        self._purge_on_register()
        entry = QueryRun()
        with _LOCK:
            self._store[cancel_id] = entry
        return entry

    def _purge_on_register(self):
        """Call purge_expired() then purge_retired() opportunistically.

        Skipped when ``_auto_purge`` is False (test mode via reset_for_tests).
        """
        if QueryRunRegistry._auto_purge:
            self.purge_expired()
            self.purge_retired()

    def complete(self, cancel_id: str, result=None):
        """Mark a run as completed and attach the result."""
        with _LOCK:
            entry = self._store.get(cancel_id)
            if entry:
                entry.status = "completed"
                entry.result = result

    def fail(self, cancel_id: str, error):
        """Mark a run as failed and attach the error."""
        with _LOCK:
            entry = self._store.get(cancel_id)
            if entry:
                entry.status = "failed"
                entry.error = error

    def get(self, cancel_id: str) -> "QueryRun | None":
        """Return the QueryRun for a cancel_id, or None.

        For an *expired* run that completed before expiring, the stored result is still
        accessible via ``entry.result``.  An expired run that never finished returns None.
        """
        with _LOCK:
            entry = self._store.get(cancel_id)
        if entry is None:
            return None
        if entry.status == "expired":
            return entry if entry.result is not None else None
        return entry

    def register_with_user(self, cancel_id: str, user: str) -> QueryRun:
        """Register a new run keyed by cancel_id and associate it with a user.

        Returns the created ``QueryRun``.
        """
        entry = self.register(cancel_id)
        with _LOCK:
            entry.user = user or ""
            self._user_map.setdefault(user, []).append(cancel_id)
        return entry

    def set_stage(self, cancel_id: str, message: str) -> bool:
        """Record the latest progress message for a run. Returns True if the run was found.

        Called from the worker's status callback, i.e. on the hot path of every query -- so it
        takes the lock briefly and does nothing else. Unknown ids are ignored rather than raising:
        a status message arriving for a purged run must never break the query that emitted it.
        """
        if not cancel_id or not message:
            return False
        text = str(message).strip()
        if not text:
            return False
        with _LOCK:
            entry = self._store.get(cancel_id)
            if entry is None:
                return False
            entry.stage = text
            entry.stage_at = _current_now()
        return True

    def in_flight_snapshot(self) -> list:
        """Plain-dict snapshot of every run that is actually still in flight, oldest first.

        Liveness is determined by presence in `_EVENTS` (live cancel handles), not database `status`. This ensures crashed workers drop out immediately via their exit handlers, rather than remaining visible for up to GRACE_TTL_SEC until the next `purge_expired()` cycle. Returns immutable dicts to prevent caller mutation of registry state."""
        now = _current_now()
        with _LOCK:
            ids = list(_EVENTS.keys())
            rows = []
            for cid in ids:
                entry = self._store.get(cid)
                if entry is None:
                    # A cancel handle with no registry entry: `begin()` runs on the request thread
                    # before the worker registers, so this is a real (brief) state, not an error.
                    rows.append({"cancel_id": cid, "user": "", "stage": "", "age_s": 0.0,
                                 "stage_age_s": None, "status": "starting"})
                    continue
                rows.append({
                    "cancel_id": cid,
                    "user": entry.user,
                    "stage": entry.stage,
                    "status": entry.status,
                    "age_s": self.___age_seconds(entry.created_at, now),
                    "stage_age_s": (None if entry.stage_at is None
                                    else self.___age_seconds(entry.stage_at, now)),
                })
        rows.sort(key=lambda r: r["age_s"], reverse=True)
        return rows

    def get_latest_for_user(self, user: str) -> "QueryRun | None":
        """Return the latest QueryRun for a user, or None if no runs exist.

        Skips expired runs that never completed (no result).  Returns the most recent
        non-discarded run in chronological order.
        """
        with _LOCK:
            ids = list(self._user_map.get(user, []))
        # Iterate newest first
        for cid in reversed(ids):
            entry = self.get(cid)
            if entry is not None:
                return entry
        return None

    def age_entry(self, cancel_id: str, seconds: float) -> bool:
        """Back-date an entry's created_at by *seconds* (test helper).

        Returns ``True`` if the entry was found and aged, ``False`` otherwise.
        Provides a proper registry-mediated way to simulate time passage
        instead of mutating ``created_at`` directly on the entry.
        """
        with _LOCK:
            entry = self._store.get(cancel_id)
        if entry is None:
            return False
        entry.created_at -= timedelta(seconds=seconds)
        return True

    def mark_expired(self, cancel_id: str) -> bool:
        """Explicitly mark an entry as 'expired' (test helper).

        Returns ``True`` if the entry was found and marked, ``False`` otherwise.
        Provides a proper registry-mediated way to change state instead of
        mutating ``entry.status`` directly on the entry object.
        """
        with _LOCK:
            entry = self._store.get(cancel_id)
        if entry is None:
            return False
        entry.status = "expired"
        return True

    def purge_expired(self, ttl_sec=None):
        """Mark long-running abandoned runs as 'expired'.

        Iterates over all runs in ``'running'`` state and marks any whose age exceeds
        *ttl_sec* (falls back to ``config.GRACE_TTL_SEC``) as ``'expired'``.  Expired
        runs are no longer reattachable but remain in the history store.

        **Design note:** Only ``'running'`` entries are expired.  Completed/failed runs
        persist indefinitely so their results remain reattachable—this is intentional to
        avoid losing finished work.  See ``test_completed_run_survives_expiry_for_reattach``.

        Returns the number of runs that were marked expired.
        """
        import config
        if ttl_sec is None:
            ttl_sec = config.GRACE_TTL_SEC
        now = _current_now()
        expired_count = 0
        with _LOCK:
            for entry in self._store.values():
                if entry.status != "running":
                    continue
                created = entry.created_at
                if created.tzinfo is None:
                    # Naive datetimes treated as UTC for consistency with time.time() semantics
                    created = created.replace(tzinfo=timezone.utc)
                age_sec = (now - created).total_seconds()
                if age_sec > ttl_sec:
                    entry.status = "expired"
                    expired_count += 1
        return expired_count

    def purge_retired(self, retention_sec=None):
        """Permanently DELETE completed/failed/expired entries older than the retention window.

        Removes matching entries from BOTH ``_store`` and ``_user_map`` so the registry
        does not grow without bound.  A ``'running'`` entry is NEVER deleted by this method,
        regardless of age — only ``purge_expired`` / ``GRACE_TTL_SEC`` govern running runs.

        Parameters
        ----------
        retention_sec : int | float | None
            Retention window in seconds.  Falls back to ``config.RUN_RETENTION_SEC`` when
            not specified.

        Returns the number of entries that were permanently deleted.
        """
        import config
        if retention_sec is None:
            retention_sec = config.RUN_RETENTION_SEC
        now = _current_now()
        deleted_count = 0
        with _LOCK:
            to_remove = [
                cid for cid, entry in self._store.items()
                if entry.status != "running"
                and self.___age_seconds(entry.created_at, now) > retention_sec
            ]
            deleted_set = set(to_remove)
            for cid in to_remove:
                del self._store[cid]
                deleted_count += 1
            # Remove retired ids from every user's list
            for uid_list in self._user_map.values():
                uid_list[:] = [cid for cid in uid_list if cid not in deleted_set]

        return deleted_count

    def ___age_seconds(self, created: datetime, now: datetime) -> float:
        """Compute age of *created* relative to *now*, treating naive as UTC."""
        ts = created
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds()

    @classmethod
    def reset_for_tests(cls):
        """Clear the singleton store and disable auto-purge — test teardown only."""
        cls._instance = None
        cls._auto_purge = False


class Cancelled(Exception):
    """Raised at a checkpoint when the current request has been cancelled."""


def begin(cancel_id):
    """Register a new cancellable request (called on the request thread before work starts)."""
    if not cancel_id:
        return None
    ev = threading.Event()
    with _LOCK:
        _EVENTS[cancel_id] = ev
    return ev


def cancel(cancel_id):
    """Signal cancellation for a request id (called from the /query/cancel thread or on client disconnect)."""
    with _LOCK:
        ev = _EVENTS.get(cancel_id)
    if ev:
        ev.set()
        return True
    return False


def end(cancel_id):
    """Drop a finished request's event (called in a finally)."""
    if not cancel_id:
        return
    with _LOCK:
        _EVENTS.pop(cancel_id, None)


def bind(cancel_id):
    """Attach THIS thread to a request's cancel event (called at the top of the worker thread)."""
    with _LOCK:
        _local.event = _EVENTS.get(cancel_id) if cancel_id else None


def unbind():
    _local.event = None


def current_event():
    """This thread's bound cancel event, or None - handed to a helper thread that must watch the same request
    (ollama_client's silence watch ends a silent model read on Stop; check() only runs between chunks)."""
    return getattr(_local, "event", None)


def is_cancelled():
    ev = getattr(_local, "event", None)
    return bool(ev is not None and ev.is_set())


def check():
    """Raise Cancelled if this thread's request has been cancelled. Call at safe boundaries."""
    if is_cancelled():
        raise Cancelled()


def active_count():
    with _LOCK:
        return len(_EVENTS)


def _clear_for_tests():
    with _LOCK:
        _EVENTS.clear()
    _local.event = None
