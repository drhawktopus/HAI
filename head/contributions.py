"""Persistent cross-user queue for user corpus contributions.

Contributions are durable (persisted to JSON) and decoupled from heavy deps.
The `writer` callable handles the actual corpus write, keeping this module
testable with a mock writer and temp directory.

Lifecycle: pending -> approved | rejected."""
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
_TITLE_CLIP = 200
_CONTENT_CLIP = 40_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    """Sortable-ish id: contrib-<epoch>-<short>."""
    return f"contrib-{int(time.time())}-{uuid.uuid4().hex[:6]}"


class ContributionQueue:
    """Thread-safe queue of corpus contributions, mirrored to <state_dir>/<id>.json (atomic, fail-soft).
    All mutations go through here so the persisted files are the ground truth after a restart."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._items: dict[str, dict] = {}
        self._seq = 0   # monotonic create-order tiebreaker
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
            cid = rec.get("id")
            if cid:
                items[cid] = rec
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

    # --- mutations ---
    def submit(self, submitter, target_namespace, title, content) -> dict | None:
        """Stage a contribution as PENDING. Returns the record, or None if there's no content."""
        content = (content or "").strip()
        if not content:
            return None
        cid = new_id()
        rec = {
            "id": cid,
            "submitter": submitter or "",
            "target_namespace": target_namespace,
            "title": ((title or "").strip()[:_TITLE_CLIP] or "Untitled"),
            "content": content[:_CONTENT_CLIP],
            "status": PENDING,
            "created_at": now_iso(),
            "decided_at": None,
            "decided_by": None,
            "result_filename": None,
        }
        with self._lock:
            self._seq += 1
            rec["_seq"] = self._seq
            self._items[cid] = rec
        self._save(rec)
        return dict(rec)

    def approve(self, cid, admin, writer) -> dict | None:
        """Approve a PENDING contribution: call `writer(target_namespace, title, content, submitter)` (which
        writes the doc into the shared corpus and returns a filename), then mark it approved. Returns the
        record, or None if it's missing/already decided. `writer` may raise — on failure the item stays
        PENDING (nothing is marked) and the exception propagates to the caller."""
        with self._lock:
            rec = self._items.get(cid)
            if rec is None or rec.get("status") != PENDING:
                return None
            snapshot = dict(rec)
        filename = writer(snapshot["target_namespace"], snapshot["title"],
                          snapshot["content"], snapshot["submitter"])
        with self._lock:
            rec = self._items.get(cid)
            if rec is None:
                return None
            rec["status"] = APPROVED
            rec["decided_at"] = now_iso()
            rec["decided_by"] = admin or ""
            rec["result_filename"] = filename
            out = dict(rec)
        self._save(out)
        return out

    def reject(self, cid, admin) -> dict | None:
        """Reject a PENDING contribution (nothing is written). Returns the record or None."""
        with self._lock:
            rec = self._items.get(cid)
            if rec is None or rec.get("status") != PENDING:
                return None
            rec["status"] = REJECTED
            rec["decided_at"] = now_iso()
            rec["decided_by"] = admin or ""
            out = dict(rec)
        self._save(out)
        return out

    # --- reads ---
    def get(self, cid) -> dict | None:
        with self._lock:
            rec = self._items.get(cid)
            return dict(rec) if rec else None

    def list(self, status=PENDING, limit=100) -> list[dict]:
        """Newest-first records, optionally filtered by status (status=None -> all)."""
        with self._lock:
            items = [dict(r) for r in self._items.values()
                     if (status is None or r.get("status") == status)]
        items.sort(key=lambda r: r.get("_seq", 0), reverse=True)
        return items[:limit]


# --- process-wide singleton + submit-time notification ---------------------------------------------
# The submit tool (tools.py) and the approve/reject endpoints (app.py) MUST share ONE queue instance so a
# submission is immediately visible to approval. Kept out of app.py (which imports tools, not vice-versa) to
# avoid a circular import; config is imported lazily so the class above stays dep-free + unit-testable.
_QUEUE = None
_QUEUE_LOCK = threading.Lock()


def queue() -> "ContributionQueue":
    """The process-wide ContributionQueue, lazily built from config.CONTRIB_STATE_DIR."""
    global _QUEUE
    if _QUEUE is None:
        with _QUEUE_LOCK:
            if _QUEUE is None:
                import config
                _QUEUE = ContributionQueue(config.CONTRIB_STATE_DIR)
    return _QUEUE


def notify(rec) -> None:
    """Fire-and-forget Discord ping when a contribution is submitted. Fail-soft; no-op if the webhook is
    unset (the default). Reuses the same webhook infra as overnight/absurd self-reports."""
    if not rec:
        return
    try:
        import config
        url = getattr(config, "CONTRIB_NOTIFY_WEBHOOK", "")
        if not url:
            return
        panel = getattr(config, "CONTRIB_PANEL_URL", "") or "the review panel"
        line = (f":books: {rec.get('submitter')} proposed adding \"{rec.get('title')}\" to the "
                f"{rec.get('target_namespace')} corpus. Review at {panel}.")
        import httpx
        httpx.post(url, json={"text": line}, timeout=10)
    except Exception:
        pass
