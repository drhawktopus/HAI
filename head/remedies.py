"""Remedies-with-consent module.

High-level:
  - Whitelisted remedies are proposed by a watch tick and approved via any interface.
  - Records persist across containers and outlive the originating conversation.

Key invariants:
  1. Atomicity under lock: approve() holds the lock during verify, execute, and state-update
     to prevent race conditions between multiple approval surfaces.
  2. Re-verify at execution time: Execution occurs only if the condition is still present.
     - Condition cleared: resolves as 'resolved_self' (no action taken).
     - Verification failed/unknown: refuses approval (fail-closed); approval remains valid
       for retry.
  3. TTL enforcement: Expired proposals are rejected explicitly to prevent stale actions.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
RESOLVED_SELF = "resolved_self"

# The whitelist. Each kind names the exact desk artifact it produces — nothing else may be
# proposed, so nothing else can ever be approved. No kills, no deletes, no deploys.
REMEDY_KINDS = {
    "stop": "queue a *.stop.json slip at the factory desk - the RUNNING order ends at its "
            "next boundary and ships what exists",
    "requeue": "queue a *.ctl.json requeue slip - the factory restores the order's newest "
               "archived generation to its queue",
}

_WHY_CLIP = 300
_DEFAULT_TTL_S = 240 * 60      # the default watch horizon (240m), for tool-filed proposals


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return f"rem-{int(time.time())}-{uuid.uuid4().hex[:6]}"


class RemedyQueue:
    """Thread-safe queue of remedy proposals, mirrored to <state_dir>/<id>.json (atomic,
    fail-soft). All mutations go through here so the persisted files are ground truth after a
    restart — a proposal filed at 2 AM must still be answerable at breakfast."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._items: dict[str, dict] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self.load()

    # --- persistence ---
    def load(self) -> None:
        items: dict[str, dict] = {}
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
            rid = rec.get("id")
            if rid:
                items[rid] = rec
        with self._lock:
            self._items = items
            self._seq = max([r.get("_seq", 0) for r in items.values()], default=0)

    def _save(self, rec: dict) -> None:
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            path = os.path.join(self.state_dir, f"{rec['id']}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    # --- expiry (called under the lock) ---
    def _expire_due_locked(self, now: float) -> None:
        """A pending proposal past its horizon becomes EXPIRED — a typed terminal fate, persisted,
        so a late approval sees WHY it cannot fire instead of a mystery miss."""
        for rec in self._items.values():
            if rec.get("status") == PENDING and now >= float(rec.get("expires_epoch") or 0):
                rec["status"] = EXPIRED
                rec["decided_at"] = now_iso()
                rec["result"] = "expired unanswered after %dm (the proposing watch's horizon)" % (
                    max(1, int((float(rec.get("expires_epoch", 0)) -
                                self._created_epoch(rec)) // 60)))
                self._save(rec)

    @staticmethod
    def _created_epoch(rec) -> float:
        try:
            return datetime.fromisoformat(rec["created_at"]).timestamp()
        except Exception:
            return 0.0

    # --- mutations ---
    def submit(self, kind, host, order, why, proposed_by, watch_id="", ttl_seconds=None) -> dict | None:
        """File a proposal as PENDING. Returns the record, an {"error": ...} dict on a
        non-whitelisted kind, or the EXISTING record when an identical proposal is already
        pending (dedup: the tick runs every 60s and must not paper the queue with copies)."""
        kind = (kind or "").strip().lower()
        if kind not in REMEDY_KINDS:
            return {"error": "remedy kind %r is not whitelisted - allowed: %s"
                             % (kind, sorted(REMEDY_KINDS))}
        host = (host or "").strip().lower()
        order = (order or "").strip()
        if not host or not order:
            return {"error": "a remedy names its exact artifact: host and order are required"}
        now = time.time()
        with self._lock:
            self._expire_due_locked(now)
            for rec in self._items.values():
                if (rec.get("status") == PENDING and rec.get("kind") == kind
                        and rec.get("host") == host and rec.get("order") == order):
                    return dict(rec)          # identical proposal already awaiting an answer
            rid = new_id()
            rec = {
                "id": rid,
                "kind": kind,
                "host": host,
                "order": order,
                "why": str(why or "")[:_WHY_CLIP],
                "preview": "%s: %s on %s - %s" % (kind.upper(), order, host, REMEDY_KINDS[kind]),
                "proposed_by": proposed_by or "",
                "watch_id": watch_id or "",
                "status": PENDING,
                "created_at": now_iso(),
                "expires_epoch": now + float(ttl_seconds or _DEFAULT_TTL_S),
                "decided_at": None,
                "decided_by": None,
                "result": None,
            }
            self._seq += 1
            rec["_seq"] = self._seq
            self._items[rid] = rec
            self._save(rec)
            return dict(rec)

    def approve(self, rid, admin, verify, execute) -> dict:
        """Approve a PENDING proposal — the whole decision runs INSIDE the lock.

        verify() -> (still_present: bool|None, detail: str). True = the condition the remedy
        exists for is still measurably there -> execute() fires and the record is APPROVED.
        False = it cleared itself -> RESOLVED_SELF, nothing fires, detail says when/what.
        None = could not read the deck -> the approval is REFUSED and NOT consumed (fail-closed;
        retry when the source answers). execute() -> a result string; if it raises, the record
        STAYS PENDING with last_error set so the yes can be retried (execution is idempotent —
        a control record / slip write is safe to repeat)."""
        with self._lock:
            self._expire_due_locked(time.time())
            rec = self._items.get(rid)
            if rec is None:
                return {"ok": False, "error": "no remedy proposal with that id"}
            if rec.get("status") == EXPIRED:
                return {"ok": False, "error": "proposal %s expired at %s - the moment passed; "
                                              "propose it again if it is still wanted"
                                              % (rid, rec.get("decided_at")),
                        "remedy": dict(rec)}
            if rec.get("status") != PENDING:
                return {"ok": False, "error": "proposal %s was already decided (%s by %s)"
                                              % (rid, rec.get("status"), rec.get("decided_by")),
                        "remedy": dict(rec)}
            try:
                present, detail = verify()
            except Exception as exc:
                present, detail = None, "verify raised %s: %s" % (type(exc).__name__, exc)
            if present is None:
                # Could-not-tell is not "go" AND not "gone" — refuse without consuming.
                rec["last_error"] = "re-verify could not read the deck: %s" % detail
                self._save(rec)
                return {"ok": False, "error": "cannot re-verify the condition right now (%s) - "
                                              "the approval was NOT consumed; try again when the "
                                              "source answers" % detail,
                        "remedy": dict(rec)}
            if present is False:
                rec["status"] = RESOLVED_SELF
                rec["decided_at"] = now_iso()
                rec["decided_by"] = admin or ""
                rec["result"] = "nothing fired - the condition cleared itself: %s" % detail
                self._save(rec)
                return {"ok": True, "remedy": dict(rec)}
            try:
                result = execute()
            except Exception as exc:
                rec["last_error"] = "execute failed: %s: %s" % (type(exc).__name__, exc)
                self._save(rec)
                return {"ok": False, "error": "remedy execution failed (%s) - the proposal is "
                                              "still pending; approving again retries"
                                              % rec["last_error"],
                        "remedy": dict(rec)}
            rec["status"] = APPROVED
            rec["decided_at"] = now_iso()
            rec["decided_by"] = admin or ""
            rec["result"] = str(result)[:500]
            self._save(rec)
            return {"ok": True, "remedy": dict(rec)}

    def reject(self, rid, admin) -> dict:
        with self._lock:
            self._expire_due_locked(time.time())
            rec = self._items.get(rid)
            if rec is None:
                return {"ok": False, "error": "no remedy proposal with that id"}
            if rec.get("status") != PENDING:
                return {"ok": False, "error": "proposal %s was already decided (%s)"
                                              % (rid, rec.get("status")),
                        "remedy": dict(rec)}
            rec["status"] = REJECTED
            rec["decided_at"] = now_iso()
            rec["decided_by"] = admin or ""
            self._save(rec)
            return {"ok": True, "remedy": dict(rec)}

    # --- reads ---
    def get(self, rid) -> dict | None:
        with self._lock:
            self._expire_due_locked(time.time())
            rec = self._items.get(rid)
            return dict(rec) if rec else None

    def list(self, status=PENDING, limit=100) -> list[dict]:
        """Newest-first records, optionally filtered by status (status=None -> all)."""
        with self._lock:
            self._expire_due_locked(time.time())
            items = [dict(r) for r in self._items.values()
                     if (status is None or r.get("status") == status)]
        items.sort(key=lambda r: r.get("_seq", 0), reverse=True)
        return items[:limit]


# --- process-wide singleton (same shape and same reason as contributions.queue()) -----------------
_QUEUE = None
_QUEUE_LOCK = threading.Lock()


def queue() -> "RemedyQueue":
    """The process-wide RemedyQueue, lazily built from config.REMEDY_STATE_DIR."""
    global _QUEUE
    if _QUEUE is None:
        with _QUEUE_LOCK:
            if _QUEUE is None:
                import config
                _QUEUE = RemedyQueue(config.REMEDY_STATE_DIR)
    return _QUEUE
