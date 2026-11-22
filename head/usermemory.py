"""Durable per-user memory — facts the assistant should ALWAYS know about a user (their project,
key details, preferences). UNLIKE conversation history/summary, this survives the 'New chat' reset and
is injected into every query, so the assistant keeps continuity across sessions (the thing users prize
about ChatGPT memory). Populated by the `remember` tool and directly editable on the Workspace share.

Stored as human-readable dated bullets at /docs/State/memory/<user>.md (NOT a RAG corpus dir, so it's
never indexed — it's injected directly)."""
import datetime
import pathlib
import re

import config

MEMORY_DIR = pathlib.Path(config.STATE_DIR) / "memory"
MAX_CHARS = int(getattr(config, "USERMEMORY_MAX_CHARS", 4000))


def _safe_user(user):
    return re.sub(r"[^A-Za-z0-9_-]", "_", user or "Guest")


def _path(user):
    return MEMORY_DIR / f"{_safe_user(user)}.md"


def load(user):
    f = _path(user)
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def add(user, note):
    """Append a dated bullet (deduped, size-capped to the most recent within MAX_CHARS)."""
    note = " ".join((note or "").split()).strip()
    if not note:
        return False
    existing = load(user)
    if note.lower() in existing.lower():     # already known -> no-op
        return True
    date = datetime.datetime.now(config.LAB_ZONE).strftime("%Y-%m-%d")
    new = (existing + "\n" if existing else "") + f"- ({date}) {note}"
    if len(new) > MAX_CHARS:                  # keep the most recent lines
        kept, total = [], 0
        for line in reversed(new.split("\n")):
            if kept and total + len(line) > MAX_CHARS:
                break
            kept.append(line)
            total += len(line)
        new = "\n".join(reversed(kept))
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        _path(user).write_text(new.strip() + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def clear(user):
    f = _path(user)
    try:
        if f.exists():
            f.unlink()
        return True
    except OSError:
        return False
