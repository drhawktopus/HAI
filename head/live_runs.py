"""In-memory event logs: the single source of truth for all chat run events (worker writes, all viewers read).

Key invariants:
- Readers poll by `seq`; a new viewer fetches all history after their last seq.
- Unsent deltas merge into the previous event to limit log size; this merges safely only while unobserved.
- Runs with a checkpoint ledger persist events at checkpoints to survive restarts with the same `seq`.
- Finished feeds persist for `config.GRACE_TTL_SEC` to accommodate late-arriving viewers."""
import json
import os
import pathlib
import threading
import time

import config

_LOCK = threading.Lock()
_FEEDS = {}        # cancel_id -> Feed
_MERGEABLE = ("content", "thinking")


class Feed:
    def __init__(self, cancel_id, user, chat_id, text):
        self.cancel_id = cancel_id
        self.user = user or ""
        self.chat_id = chat_id or ""
        self.text = text or ""
        self.started = time.time()
        self.events = []
        self.sealed = 0          # events below this index have been handed to a reader: never edit them
        self.done = False
        self.finished_at = None
        self.last_status = ""
        self.epoch = 0           # +1 each time the run is restored after a restart
        self.restored_upto = -1  # last seq carried over from the snapshot; a viewer past it has a stale tail

    def summary(self):
        return {"cancel_id": self.cancel_id, "user": self.user, "chat_id": self.chat_id, "text": self.text,
                "started": self.started, "elapsed_s": int(time.time() - self.started), "done": self.done,
                "status": self.last_status}


def open_feed(cancel_id, user, chat_id, text, restore_from=None):
    """Start the feed for a run. If `restore_from` is provided, the feed resumes from the given snapshot to maintain continuity for viewers."""
    purge()
    feed = Feed(cancel_id, user, chat_id, text)
    snap = None
    if restore_from:
        try:
            snap = json.loads(pathlib.Path(restore_from).read_text(encoding="utf-8"))
        except Exception:
            snap = None
    if isinstance(snap, dict) and isinstance(snap.get("events"), list):
        feed.events = [e for e in snap["events"] if isinstance(e, dict)]
        feed.epoch = int(snap.get("epoch") or 0) + 1
        feed.restored_upto = len(feed.events) - 1
        feed.sealed = len(feed.events)
        feed.started = float(snap.get("started") or feed.started)
    with _LOCK:
        _FEEDS[cancel_id] = feed
    return feed


def snapshot(cancel_id, path):
    """Save the feed beside the run's checkpoint. Called at every checkpoint (before each model turn), when the
    feed holds only finished turns: a restore never brings back half a turn."""
    with _LOCK:
        feed = _FEEDS.get(cancel_id)
        if feed is None:
            return
        data = {"epoch": feed.epoch, "started": feed.started, "events": [dict(e) for e in feed.events]}
        feed.sealed = len(feed.events)    # what is on disk must never change under a reader
    try:
        path = pathlib.Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass   # a snapshot that cannot be written costs a seamless view, never the run


def push(cancel_id, event):
    """Append one event. Unknown or finished runs are ignored: an event from a late thread must never
    break the run that emitted it."""
    if not cancel_id or not isinstance(event, dict):
        return
    with _LOCK:
        feed = _FEEDS.get(cancel_id)
        if feed is None or feed.done:
            return
        if event.get("type") == "status":
            feed.last_status = str(event.get("message") or "")
        last = feed.events[-1] if feed.events else None
        if (last is not None and len(feed.events) - 1 >= feed.sealed and event.get("type") == "delta"
                and last.get("type") == "delta" and event.get("kind") in _MERGEABLE
                and last.get("kind") == event.get("kind") and last.get("turn") == event.get("turn")):
            last["text"] = (last.get("text") or "") + (event.get("text") or "")
            return
        feed.events.append(dict(event, seq=len(feed.events)))


def finish(cancel_id, result):
    """The run's last event: its result. Every reader ends on it."""
    with _LOCK:
        feed = _FEEDS.get(cancel_id)
        if feed is None or feed.done:
            return
        feed.events.append({"type": "result", "result": result, "seq": len(feed.events)})
        feed.done = True
        feed.finished_at = time.time()


def read(cancel_id, after=0):
    """(events with seq >= after, done, feed) - feed None when the run is unknown or purged. Handing
    events to a reader seals them, so they can no longer be merged into."""
    with _LOCK:
        feed = _FEEDS.get(cancel_id)
        if feed is None:
            return [], True, None
        after = max(0, int(after or 0))
        out = [dict(e) for e in feed.events[after:]]
        feed.sealed = max(feed.sealed, len(feed.events))
        return out, feed.done, feed


def get(cancel_id):
    with _LOCK:
        return _FEEDS.get(cancel_id)


def live_ids():
    """cancel_ids of every run still working."""
    with _LOCK:
        return {cid for cid, f in _FEEDS.items() if not f.done}


def active(user, chat_id=None):
    """Summaries of this user's runs still working (optionally one chat's), oldest first."""
    with _LOCK:
        rows = [f.summary() for f in _FEEDS.values()
                if not f.done and f.user == (user or "") and (chat_id is None or f.chat_id == chat_id)]
    return sorted(rows, key=lambda r: r["started"])


def purge(now=None):
    """Drop finished feeds older than config.GRACE_TTL_SEC. A feed still working is never dropped:
    its worker finishes it on every route (the caller's finally)."""
    now = time.time() if now is None else now
    with _LOCK:
        for cid in [cid for cid, f in _FEEDS.items()
                    if f.done and now - (f.finished_at or now) > config.GRACE_TTL_SEC]:
            del _FEEDS[cid]


def _clear_for_tests():
    with _LOCK:
        _FEEDS.clear()
