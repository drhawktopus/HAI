"""Phase 2 (UI redesign): registry + persistence for asynchronous "Absurd" effort runs.

An Absurd run is the SAME think-hard convergence engine (app._run_think_hard) driven with a much larger
time budget and run DETACHED: the user fires it, gets a run id back immediately, and is notified when it
finishes (Discord + an in-UI status poll). This module owns only the run BOOKKEEPING -- an in-memory
registry mirrored to one JSON file per run under a state dir, restored on boot -- so it stays free of the
heavy deps (fastapi/ollama) and is unit-testable with a temp dir. The actual model work lives in app.py.

Lifecycle: queued -> running -> done | error. `deferred` is a terminal state set at launch when an
energy-gated (non-unbounded) user tries to start while the price is above the ceiling (nothing runs).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
DEFERRED = "deferred"
SUSPENDED = "suspended"   # #37: stopped at a natural point by an operator; POST /runs/resume continues it
ACTIVE_STATUSES = (QUEUED, RUNNING)
_TEXT_CLIP = 2000
_ANSWER_CLIP = 60_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Sortable-ish id: abr-<epoch>-<short>. The epoch prefix makes newest-first ordering trivial."""
    return f"abr-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def seconds_for_choice(choices, key):
    """Map a UI time-choice key (e.g. '4h') to its seconds, or None if unknown. `choices` is the
    config.ABSURD_TIME_CHOICES list of (key, label, seconds)."""
    for k, _label, secs in choices:
        if k == key:
            return int(secs)
    return None


def active_runs_view(records, now=None):
    """Project active-run records to the small cross-user view the deck's live-runs panel shows.

    Deliberately EXCLUDES the full packet `text` (may be large/sensitive) — a live-runs list only
    needs identity + progress. `elapsed_s` is whole seconds since `started_at`, or None if the run
    has not started yet or the timestamp is unparseable (never raises). `now` is injectable for tests.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    view = []
    for r in records:
        elapsed = None
        started = r.get("started_at")
        if started:
            try:
                dt = datetime.fromisoformat(started)
                elapsed = max(0, int((now - dt).total_seconds()))
            except Exception:
                elapsed = None
        view.append({
            "run_id": r.get("run_id"),
            "title": r.get("title") or r.get("run_id"),
            "status": r.get("status"),
            "last_status": r.get("last_status") or "",
            "user": r.get("user"),
            "elapsed_s": elapsed,
        })
    return view


class AbsurdRegistry:
    """Thread-safe registry of Absurd runs, mirrored to <state_dir>/<run_id>.json (atomic writes,
    fail-soft). All mutations go through here so the persisted files are the ground truth after a
    restart."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._runs: dict[str, dict] = {}
        self._seq = 0   # monotonic create-order tiebreaker (created_at is only second-resolution)
        self._lock = threading.Lock()
        self.load()

    # --- persistence ---
    def load(self) -> None:
        """Restore all persisted runs into memory. Any run left mid-flight by a crash/restart is marked
        ERROR (interrupted) -- a detached thread does not survive a process restart, so it can't be
        'running' anymore."""
        runs: dict[str, dict] = {}
        try:
            names = [n for n in os.listdir(self.state_dir) if n.endswith(".json")]
        except OSError:
            names = []
        for n in names:
            try:
                with open(os.path.join(self.state_dir, n), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except Exception:
                continue
            rid = rec.get("run_id")
            if not rid:
                continue
            if rec.get("status") in ACTIVE_STATUSES:
                rec["status"] = ERROR
                rec["error"] = "interrupted by an orchestrator restart"
                rec["finished_at"] = rec.get("finished_at") or now_iso()
            runs[rid] = rec
        with self._lock:
            self._runs = runs
            self._seq = max([r.get("_seq", 0) for r in runs.values()], default=0)

    def _save(self, rec: dict) -> None:
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            path = os.path.join(self.state_dir, f"{rec['run_id']}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    # --- mutations ---
    def create(self, run_id, user, text, choice, max_seconds, status=QUEUED, **extra) -> dict:
        rec = {
            "run_id": run_id,
            "user": user,
            "text": (text or "")[:_TEXT_CLIP],
            "choice": choice,
            "max_seconds": int(max_seconds),
            "status": status,
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "last_status": "",
            "answer": None,
            "error": None,
        }
        rec.update(extra)
        with self._lock:
            self._seq += 1
            rec["_seq"] = self._seq
            self._runs[run_id] = rec
        self._save(rec)
        return dict(rec)

    def update(self, run_id, **fields) -> dict | None:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            if "answer" in fields and isinstance(fields["answer"], str):
                fields["answer"] = fields["answer"][:_ANSWER_CLIP]
            rec.update(fields)
            snapshot = dict(rec)
        self._save(snapshot)
        return snapshot

    # --- reads ---
    def get(self, run_id) -> dict | None:
        with self._lock:
            rec = self._runs.get(run_id)
            return dict(rec) if rec else None

    def list_for(self, user, limit=20) -> list[dict]:
        with self._lock:
            mine = [dict(r) for r in self._runs.values() if r.get("user") == user]
        mine.sort(key=lambda r: r.get("_seq", 0), reverse=True)   # newest-first, collision-proof
        return mine[:limit]

    def list_active_all(self, limit=50) -> list[dict]:
        """All runs (ANY user) that are in progress (queued|running), newest-first. The deck's
        live-runs panel needs a cross-user view; list_for is per-user and /absurd/status only ever
        returns the caller's own runs. Reuses ACTIVE_STATUSES so 'which statuses are live' has ONE
        definition, shared with has_active()."""
        with self._lock:
            active = [dict(r) for r in self._runs.values() if r.get("status") in ACTIVE_STATUSES]
        active.sort(key=lambda r: r.get("_seq", 0), reverse=True)
        return active[:limit]

    def has_active(self, user=None) -> bool:
        """True if any run (optionally for one user) is queued or running -- used to keep one big
        Absurd run on the shared 27B at a time."""
        with self._lock:
            for r in self._runs.values():
                if r.get("status") in ACTIVE_STATUSES and (user is None or r.get("user") == user):
                    return True
        return False
